import argparse
import json
import sqlite3
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import torch

from scripts import build_research_semantic_index as semantic_builder
from scripts import query_research_ledger as query_module
from scripts.build_research_ledger_index import build as build_ledger
from scripts.build_research_semantic_index import (
    ENCODER_ORACLE_EXPECTED,
    checkpoint_bridge_layout,
    encode_texts,
    event_text,
    events_and_fingerprint,
    initialize,
    normalize_vectors,
    repair_encoder_nonpersistent_buffers,
    restore_encoder_checkpoint_weights,
    select_encoder_smoke_texts,
    source_passages,
    validate_encoder_model_card_oracle,
)
from scripts.query_research_ledger import hybrid_query, semantic_query


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def event(event_id: str, summary: str) -> dict:
    return {
        "schema_version": "1.0", "event_id": event_id, "date": "2026-07-19", "event_type": "gate_result",
        "status": "completed", "project": "test", "workstream": "test", "summary": summary,
        "relations": [], "artifacts": [],
        "source": {"source_path": "docs/source.md", "source_sha256": "abc", "heading": event_id,
                   "line_start": 1, "line_end": 2, "legacy_import": False, "requires_human_review": False},
    }


def test_event_text_and_source_fingerprint_are_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "research-events"
    write_jsonl(root / "sources.jsonl", [{"source_id": "src", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}])
    sample = event("evt_1", "Qwen evidence extraction continued")
    write_jsonl(root / "daily" / "2026-07-19" / "events.jsonl", [sample])
    events, first = events_and_fingerprint(root)
    _, second = events_and_fingerprint(root)
    assert first == second
    assert "Title: evt_1" in event_text(events[0])
    assert "Summary: Qwen evidence extraction continued" in event_text(events[0])


def test_events_apply_append_only_source_range_correction_without_rewriting_jsonl(
    tmp_path: Path,
) -> None:
    root = tmp_path / "research-events"
    write_jsonl(root / "sources.jsonl", [{"source_id": "src", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}])
    original = event("evt_original", "Original range")
    original["source"]["line_end"] = 3
    correction = event("evt_correction", "Corrected range")
    correction.update(
        {
            "event_type": "amendment",
            "relations": [{"type": "corrects", "target": "evt_original"}],
            "observed": {
                "corrected_event_id": "evt_original",
                "corrected_json_pointer": "/source/line_end",
                "recorded_line_end": 3,
                "corrected_line_end": 2,
            },
        }
    )
    events_path = root / "daily" / "2026-08-03" / "events.jsonl"
    write_jsonl(events_path, [original, correction])
    canonical_before = events_path.read_bytes()

    events, _ = events_and_fingerprint(root)

    assert events_path.read_bytes() == canonical_before
    assert next(row for row in events if row["event_id"] == "evt_original")["source"]["line_end"] == 2


def test_hybrid_query_fuses_lexical_and_semantic_candidates(tmp_path: Path) -> None:
    root = tmp_path / "research-events"
    write_jsonl(root / "sources.jsonl", [{"source_id": "src", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}])
    rows = [event("evt_qwen", "Qwen evidence extraction continued"), event("evt_minilm", "MiniLM baseline result")]
    write_jsonl(root / "daily" / "2026-07-19" / "events.jsonl", rows)
    ledger = root / "index" / "research.sqlite"
    build_ledger(root, ledger)
    semantic = root / "index" / "semantic.sqlite"
    with sqlite3.connect(semantic) as connection:
        initialize(connection)
        connection.executemany("INSERT INTO embeddings VALUES (?, ?, ?, ?)", [
            ("evt_qwen", 2, np.array([0.9, 0.0], dtype=np.float32).tobytes(), "q"),
            ("evt_minilm", 2, np.array([0.0, 1.0], dtype=np.float32).tobytes(), "m"),
        ])
        connection.execute(
            "INSERT INTO passage_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "psg_qwen", "evt_qwen", "docs/source.md", "Qwen evidence", 10, 12, 2,
                np.array([1.0, 0.0], dtype=np.float32).tobytes(), "p",
            ),
        )
        connection.execute("INSERT INTO metadata VALUES (?, ?)", ("dimensions", "2"))
        connection.commit()
    args = argparse.Namespace(query="Qwen", date=None, status=None, event_type=None, source_path=None, related_to=None, limit=2)
    with sqlite3.connect(ledger) as ledger_connection, sqlite3.connect(semantic) as semantic_connection:
        results = hybrid_query(ledger_connection, semantic_connection, args, np.array([1.0, 0.0], dtype=np.float32))
    assert results[0]["event_id"] == "evt_qwen"
    assert results[0]["retrieval"]["cosine_similarity"] == 1.0
    assert results[0]["retrieval"]["semantic_evidence"]["line_start"] == 10
    with sqlite3.connect(ledger) as ledger_connection, sqlite3.connect(semantic) as semantic_connection:
        dense_results = semantic_query(
            ledger_connection, semantic_connection, args, np.array([1.0, 0.0], dtype=np.float32)
        )
    assert dense_results[0]["event_id"] == "evt_qwen"
    assert dense_results[0]["retrieval"]["semantic_evidence"]["line_end"] == 12


def test_source_passages_keep_provenance_and_cap_long_sections(tmp_path: Path) -> None:
    source = tmp_path / "docs" / "source.md"
    source.parent.mkdir(parents=True)
    source.write_text("\n\n".join(f"paragraph {index} evidence" for index in range(10)), encoding="utf-8")
    row = event("evt_source", "summary")
    row["source"].update({"source_path": "docs/source.md", "line_start": 1, "line_end": 19})
    passages = source_passages(row, tmp_path, maximum=4, target_chars=20)
    assert len(passages) == 4
    assert passages[0]["source_path"] == "docs/source.md"
    assert passages[0]["line_start"] == 1
    assert passages[-1]["line_end"] == 19


def test_normalize_vectors_rejects_non_finite_encoder_output() -> None:
    vectors = np.array([[1.0, 0.0], [np.nan, 1.0]], dtype=np.float32)

    try:
        normalize_vectors(vectors, dimensions=2)
    except ValueError as exc:
        assert "non-finite vectors in 1 rows" in str(exc)
    else:
        raise AssertionError("Expected non-finite encoder output to be rejected")


def test_source_passages_use_exact_event_line_for_canonical_daily_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "research-events" / "daily" / "2026-07-27" / "events.jsonl"
    first = event("evt_first", "first summary")
    second = event("evt_second", "second summary")
    first["source"] = {"source_path": "research-events/daily/2026-07-27/events.jsonl"}
    second["source"] = {"source_path": "research-events/daily/2026-07-27/events.jsonl"}
    write_jsonl(path, [first, second])

    passages = source_passages(second, tmp_path)

    assert len(passages) == 1
    assert passages[0]["event_id"] == "evt_second"
    assert passages[0]["line_start"] == 2
    assert passages[0]["line_end"] == 2
    assert "second summary" in passages[0]["text"]
    assert "first summary" not in passages[0]["text"]


def test_encoder_smoke_selection_is_shortest_median_longest() -> None:
    texts = ["middle" * 10, "s", "long" * 100, "tiny"]

    assert select_encoder_smoke_texts(texts) == ["s", "middle" * 10, "long" * 100]


class _FakeSentenceModel:
    def __init__(self, base_model: torch.nn.Module) -> None:
        self.transformer = types.SimpleNamespace(auto_model=base_model)

    def __getitem__(self, index: int):
        if index != 0:
            raise IndexError(index)
        return self.transformer


def test_checkpoint_bridge_strictly_restores_prefixed_base_weights(tmp_path: Path) -> None:
    from safetensors.torch import save_file

    base = torch.nn.Module()
    base.linear = torch.nn.Linear(2, 2)
    expected_weight = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    expected_bias = torch.tensor([5.0, 6.0])
    save_file(
        {
            "new.linear.weight": expected_weight,
            "new.linear.bias": expected_bias,
            "classifier.weight": torch.zeros((1, 2)),
            "classifier.bias": torch.zeros(1),
        },
        str(tmp_path / "model.safetensors"),
    )

    count = restore_encoder_checkpoint_weights(_FakeSentenceModel(base), tmp_path)

    assert count == 2
    assert torch.equal(base.linear.weight, expected_weight)
    assert torch.equal(base.linear.bias, expected_bias)


def test_checkpoint_bridge_rejects_changed_exclusions(tmp_path: Path) -> None:
    from safetensors.torch import save_file

    save_file(
        {
            "new.weight": torch.ones(1),
            "classifier.weight": torch.ones(1),
            "classifier.bias": torch.ones(1),
            "unexpected.head": torch.ones(1),
        },
        str(tmp_path / "model.safetensors"),
    )

    with pytest.raises(ValueError, match="exclusions changed"):
        checkpoint_bridge_layout(tmp_path)


def test_nonpersistent_buffer_repair_rebuilds_position_and_rope() -> None:
    class FakeRotary(torch.nn.Module):
        def __init__(self, config) -> None:
            super().__init__()
            inv_freq = torch.tensor([1.0, 0.5], dtype=torch.float32)
            positions = torch.arange(
                int(config.max_position_embeddings * config.rope_scaling["factor"]),
                dtype=torch.float32,
            )
            angles = torch.outer(positions, inv_freq)
            doubled = torch.cat((angles, angles), dim=-1)
            self.register_buffer("inv_freq", inv_freq, persistent=False)
            self.register_buffer("cos_cached", doubled.cos(), persistent=False)
            self.register_buffer("sin_cached", doubled.sin(), persistent=False)

    class FakeEmbeddings(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.word_embeddings = torch.nn.Embedding(8, 4)
            self.register_buffer("position_ids", torch.full((8,), 99), persistent=False)
            self.rotary_emb = FakeRotary(config)

        def _init_rope(self, value) -> None:
            self.rotary_emb = FakeRotary(value)

    config = types.SimpleNamespace(
        max_position_embeddings=8,
        rope_scaling={"factor": 2.0, "type": "ntk"},
    )
    base = torch.nn.Module()
    base.config = config
    base.embeddings = FakeEmbeddings()
    base.embeddings.rotary_emb.inv_freq.fill_(float("nan"))

    count = repair_encoder_nonpersistent_buffers(_FakeSentenceModel(base))
    buffers = dict(base.named_buffers())

    assert count == 4
    assert torch.equal(buffers["embeddings.position_ids"], torch.arange(8))
    assert torch.isfinite(buffers["embeddings.rotary_emb.inv_freq"]).all()
    assert torch.equal(
        buffers["embeddings.rotary_emb.cos_cached"][0],
        torch.ones(4),
    )
    assert torch.equal(
        buffers["embeddings.rotary_emb.sin_cached"][0],
        torch.zeros(4),
    )


def test_model_card_oracle_accepts_expected_scores_and_rejects_drift() -> None:
    class FakeOracleModel:
        def __init__(self, scores: np.ndarray) -> None:
            self.scores = scores

        def encode(self, _texts, **_kwargs):
            vectors = np.zeros((4, 4), dtype=np.float32)
            vectors[0, 0] = 1.0
            for index, score in enumerate(self.scores, start=1):
                vectors[index, 0] = score
                vectors[index, 1] = np.sqrt(1.0 - float(score) ** 2)
            return vectors

    assert validate_encoder_model_card_oracle(FakeOracleModel(ENCODER_ORACLE_EXPECTED)) == 0.0
    with pytest.raises(ValueError, match="model-card oracle failed"):
        validate_encoder_model_card_oracle(
            FakeOracleModel(ENCODER_ORACLE_EXPECTED + np.float32(0.1))
        )


def test_encode_texts_propagates_explicit_dtype_and_smokes_before_full(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {"calls": []}

    class FakeSentenceTransformer:
        def __init__(self, *_args, model_kwargs, **_kwargs) -> None:
            captured["dtype"] = model_kwargs["torch_dtype"]
            self.parameter = torch.nn.Parameter(
                torch.ones(1, dtype=model_kwargs["torch_dtype"])
            )

        def parameters(self):
            return iter([self.parameter])

        def modules(self):
            return []

        def encode(self, texts, **_kwargs):
            captured["calls"].append(list(texts))
            return np.ones((len(texts), 4), dtype=np.float32)

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setattr(
        semantic_builder,
        "apply_encoder_compatibility_bridge",
        lambda *_args: {"restored_tensor_count": 2},
    )
    texts = ["s", "medium" * 10, "long" * 100]

    vectors = encode_texts(
        texts,
        tmp_path,
        "cpu",
        batch_size=2,
        max_length=32,
        dimensions=2,
        model_kwargs={"code_revision": "test"},
        encoder_dtype="float32",
    )

    assert captured["dtype"] == torch.float32
    assert captured["calls"] == [select_encoder_smoke_texts(texts), texts]
    assert vectors.shape == (3, 2)
    assert np.isfinite(vectors).all()


def test_encode_texts_aborts_on_non_finite_smoke_before_full(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []

    class NonFiniteSentenceTransformer:
        def __init__(self, *_args, model_kwargs, **_kwargs) -> None:
            self.parameter = torch.nn.Parameter(
                torch.ones(1, dtype=model_kwargs["torch_dtype"])
            )

        def parameters(self):
            return iter([self.parameter])

        def modules(self):
            return []

        def encode(self, texts, **_kwargs):
            calls.append(list(texts))
            return np.full((len(texts), 4), np.nan, dtype=np.float32)

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=NonFiniteSentenceTransformer),
    )
    monkeypatch.setattr(
        semantic_builder,
        "apply_encoder_compatibility_bridge",
        lambda *_args: {"restored_tensor_count": 2},
    )

    with pytest.raises(ValueError, match="non-finite"):
        encode_texts(
            ["short", "medium" * 10, "long" * 100],
            tmp_path,
            "cpu",
            batch_size=2,
            max_length=32,
            dimensions=2,
            encoder_dtype="float32",
        )

    assert len(calls) == 1


def test_build_records_encoder_dtype_and_smoke_count(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "research-events"
    write_jsonl(
        root / "sources.jsonl",
        [{"source_id": "src", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}],
    )
    write_jsonl(
        root / "daily" / "2026-07-19" / "events.jsonl",
        [event("evt_dtype", "dtype provenance")],
    )
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    monkeypatch.setattr(
        semantic_builder, "snapshot_model", lambda *_args: (snapshot, "test-revision")
    )
    monkeypatch.setattr(semantic_builder, "resolve_torch_device", lambda _device: "cpu")
    monkeypatch.setattr(
        semantic_builder, "configure_hf_modules_cache", lambda path: path
    )
    monkeypatch.setattr(semantic_builder, "remote_code_kwargs", lambda *_args: {})
    monkeypatch.setattr(semantic_builder, "snapshot_hashes", lambda _path: {})
    monkeypatch.setattr(
        semantic_builder, "remote_code_dependencies", lambda _path: []
    )
    monkeypatch.setattr(
        semantic_builder, "checkpoint_bridge_layout", lambda _path: (["weight"], [])
    )
    monkeypatch.setattr(
        semantic_builder,
        "encode_texts",
        lambda texts, *_args, **_kwargs: np.tile(
            np.array([[1.0, 0.0]], dtype=np.float32), (len(texts), 1)
        ),
    )
    database = root / "index" / "semantic.sqlite"
    manifest_path = root / "index" / "semantic-manifest.json"

    report = semantic_builder.build(
        root,
        database,
        manifest_path,
        "test/model",
        "test-revision",
        "cpu",
        32,
        2,
        2,
        False,
        "float32",
    )

    with sqlite3.connect(database) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["encoder_dtype"] == "float32"
    assert metadata["encoder_dtype"] == "float32"
    assert metadata["encoder_smoke_count"] == "1"
    assert manifest["encoder_dtype"] == "float32"
    assert manifest["encoder_smoke_count"] == "1"


def test_query_loader_uses_recorded_encoder_dtype(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class FakeSentenceTransformer:
        def __init__(self, *_args, model_kwargs, **_kwargs) -> None:
            captured["dtype"] = model_kwargs["torch_dtype"]
            self.parameter = torch.nn.Parameter(
                torch.ones(1, dtype=model_kwargs["torch_dtype"])
            )

        def parameters(self):
            return iter([self.parameter])

        def modules(self):
            return []

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setattr(query_module, "resolve_torch_device", lambda _device: "cpu")
    monkeypatch.setattr(query_module, "remote_code_kwargs", lambda _snapshot: {})
    monkeypatch.setattr(
        query_module,
        "apply_encoder_compatibility_bridge",
        lambda *_args: {"restored_tensor_count": 2},
    )
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    modules_cache = tmp_path / "hf-modules"
    modules_cache.mkdir()
    semantic = tmp_path / "semantic.sqlite"
    with sqlite3.connect(semantic) as connection:
        initialize(connection)
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("snapshot_path", str(snapshot)),
                ("hf_modules_cache", str(modules_cache)),
                ("max_length", "32"),
                ("dimensions", "2"),
                ("encoder_dtype", "float32"),
            ],
        )
        model, dimensions = query_module.load_semantic_encoder(connection, "cpu")

    assert model is not None
    assert dimensions == 2
    assert captured["dtype"] == torch.float32
