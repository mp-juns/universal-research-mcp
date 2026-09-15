"""Historical references remain searchable without becoming evidence."""
import json
from pathlib import Path

import pytest

from universal_research_mcp import server
from universal_research_mcp.indexing.lexical import ensure_lexical_index, index_status
from universal_research_mcp.indexing.semantic import ensure_semantic_index
from universal_research_mcp.semantic_backends import SignedHashingEmbedder
from test_lexical_index_foundation import write_populated_fixture


@pytest.mark.parametrize('kind', ['verified', 'legacy_candidate_only', 'unregistered_revision'])
def test_historical_candidate_build_search_and_gate(tmp_path: Path, kind: str):
    events, daily, digest = write_populated_fixture(tmp_path)
    event = json.loads(daily.read_text())
    if kind == 'legacy_candidate_only':
        event['source']['source_sha256'] = None
    elif kind == 'unregistered_revision':
        # Bytes match the event but the registry does not prove that revision.
        # This must remain blocked even though a current-file hash would pass.
        (events / 'sources.jsonl').write_text('')
    daily.write_text(json.dumps(event) + '\n')
    original = {p: p.read_bytes() for p in events.rglob('*.jsonl')}
    report = ensure_lexical_index(tmp_path)
    assert report['status'] == 'current'
    audit = report['verification']
    assert audit['source_evidence_eligible_count'] == int(kind == 'verified')
    assert audit['legacy_candidate_only_count'] == int(kind == 'legacy_candidate_only')
    assert audit['unregistered_revision_count'] == int(kind == 'unregistered_revision')
    assert index_status(tmp_path)['evidence_health']['status'] == ('healthy' if kind == 'verified' else 'degraded')
    if kind != 'verified':
        assert audit['evidence_diagnostics'][0]['eligibility'] == kind
    from universal_research_mcp.runtime.semantic_config import configure_demo
    configure_demo(tmp_path, dimensions=32)
    semantic = ensure_semantic_index(tmp_path, embedder=SignedHashingEmbedder(32),
                                     provider_id=SignedHashingEmbedder.provider_id, model=SignedHashingEmbedder.model, dimensions=32)
    assert semantic['status'] == 'current'
    previous = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        server.configure_runtime(tmp_path)
        for mode in ('lexical', 'semantic', 'hybrid'):
            result = server.memory_search_candidates('automatic lexical fixture', mode=mode)['results'][0]
            assert result['event_id'] == 'evt_fixture'
            assert result['evidence_eligible'] is (kind == 'verified')
            assert result['source_eligibility'] == kind
        reference = dict(path='docs/evidence.md', start_line=1, end_line=3,
                         event_id='evt_fixture', expected_sha256=digest)
        if kind == 'verified':
            assert server.memory_fetch_evidence(**reference)['integrity_status'] == 'matched'
        else:
            with pytest.raises(ValueError, match='not registered'):
                server.memory_fetch_evidence(**reference)
        receipt = server.memory_check_evidence_eligibility('Fixture result', 'result', 'material', [reference])
        assert receipt['status'] == ('eligible' if kind == 'verified' else 'blocked')
    finally:
        server.configure_runtime(*previous)
    assert all(p.read_bytes() == content for p, content in original.items())


def test_staged_verification_failure_preserves_index_and_canonical(tmp_path: Path):
    from unittest.mock import patch
    events, daily, _ = write_populated_fixture(tmp_path)
    ensure_lexical_index(tmp_path)
    database = tmp_path / 'data/index/research.sqlite'
    previous_db = database.read_bytes()
    event = json.loads(daily.read_text())
    event['summary'] = 'A changed candidate requires rebuilding'
    daily.write_text(json.dumps(event) + '\n')
    before = {p: p.read_bytes() for p in events.rglob('*.jsonl')}
    with patch('universal_research_mcp.indexing.lexical.verify_lexical_index', side_effect=RuntimeError('verification failed')):
        with pytest.raises(RuntimeError, match='verification failed'):
            ensure_lexical_index(tmp_path)
    assert database.read_bytes() == previous_db
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not list(database.parent.glob('*.staging'))


def test_legacy_encoder_settings_survive_configuration(tmp_path: Path):
    from universal_research_mcp.runtime.semantic_config import write_semantic_config
    from universal_research_mcp.semantic_runtime import configured_backend
    config = {
        'schema_version': 'semantic-retrieval-config/1.0',
        'auto_refresh': False,
        'backend': {'kind': 'local_sentence_transformer', 'model_path': str(tmp_path / 'model'),
                    'device': 'cuda', 'dimensions': 256, 'trust_local_model_code': True,
                    'encoder_dtype': 'float32', 'max_length': 512},
    }
    write_semantic_config(tmp_path, config)
    first = configured_backend(tmp_path)
    assert first.embedder.max_length == 512
    assert first.embedder.encoder_dtype == 'float32'
    config['backend']['max_length'] = 256
    write_semantic_config(tmp_path, config)
    second = configured_backend(tmp_path)
    assert first.model != second.model
    assert first.embedder is not second.embedder


def test_gte_runtime_buffer_repair_preserves_checkpoint_weights():
    from types import SimpleNamespace
    torch = pytest.importorskip('torch')
    from universal_research_mcp.semantic_backends import _restore_gte_runtime_buffers

    class NewEmbeddings(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.word_embeddings = torch.nn.Embedding(8, 4)
            self.position_embedding_type = 'rope'
            self.register_buffer('position_ids', torch.full((16,), 999), persistent=False)
            self.rotary_emb = torch.nn.Module()
            self.rotary_emb.register_buffer('inv_freq', torch.full((2,), float('nan')), persistent=False)

        def _init_rope(self, config):
            self.rotary_emb = torch.nn.Module()
            self.rotary_emb.register_buffer('inv_freq', torch.tensor([1., .01]), persistent=False)

    class NewModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.config = SimpleNamespace(model_type='new', max_position_embeddings=16)
            self.embeddings = NewEmbeddings()

    model = NewModel()
    before = {k: v.clone() for k, v in model.state_dict().items()}
    _restore_gte_runtime_buffers(model)
    assert torch.equal(model.embeddings.position_ids, torch.arange(16))
    assert torch.isfinite(model.embeddings.rotary_emb.inv_freq).all()
    assert all(torch.equal(before[k], v) for k, v in model.state_dict().items())


def test_semantic_chunk_returns_exact_parent_locator(tmp_path: Path, monkeypatch):
    import hashlib
    import sqlite3
    from universal_research_mcp.runtime.semantic_config import configure_demo
    events, daily, _ = write_populated_fixture(tmp_path)
    source = tmp_path / 'docs/evidence.md'
    source.write_text(''.join(f'Line {i}: needle protocol ' + 'bounded content ' * 12 + '\n' for i in range(1, 81)))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = json.loads((events / 'sources.jsonl').read_text())
    manifest['source_sha256'] = digest
    (events / 'sources.jsonl').write_text(json.dumps(manifest) + '\n')
    event = json.loads(daily.read_text())
    event['source'].update(source_sha256=digest, line_start=1, line_end=80)
    daily.write_text(json.dumps(event) + '\n')
    ensure_lexical_index(tmp_path)
    configure_demo(tmp_path, dimensions=32)
    ensure_semantic_index(tmp_path, SignedHashingEmbedder(32), provider_id=SignedHashingEmbedder.provider_id,
                          model=SignedHashingEmbedder.model, dimensions=32)
    with sqlite3.connect(tmp_path / 'data/index/semantic.sqlite') as db:
        dim, blob = db.execute('SELECT dimensions, vector FROM passage_embeddings LIMIT 1').fetchone()
    monkeypatch.setattr(server, '_semantic_query_vector', lambda *args: server._read_vector(blob, dim))
    previous = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        server.configure_runtime(tmp_path)
        candidate = server.memory_search_candidates('needle', mode='semantic')['results'][0]
        assert (candidate['start_line'], candidate['end_line']) == (1, 80)
        assert candidate['source_sha256'] == digest
        assert candidate['retrieval']['semantic_chunk_range']['end_line'] < 80
        fetched = server.memory_fetch_evidence('docs/evidence.md', 1, 80, 0, 'evt_fixture', digest)
        assert fetched['integrity_status'] == 'matched'
    finally:
        server.configure_runtime(*previous)
