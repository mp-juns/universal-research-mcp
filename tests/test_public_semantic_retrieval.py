"""Public offline semantic/hybrid retrieval contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from universal_research_mcp import cli, server
from universal_research_mcp.indexing import ensure_lexical_index
from universal_research_mcp.runtime.research_profile import profile_template, write_profile


def _fixture(root: Path) -> None:
    source = root / "docs/evidence.md"
    source.parent.mkdir(parents=True)
    source.write_text("alpha semantic evidence\nbeta lexical evidence\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    events = root / "data/events"
    events.mkdir(parents=True)
    (events / "sources.jsonl").write_text(json.dumps({
        "source_id": "src_evidence", "source_path": "docs/evidence.md",
        "source_sha256": digest, "source_type": "markdown", "legacy_import": False,
    }) + "\n", encoding="utf-8")
    daily = events / "daily/2026-08-13/events.jsonl"
    daily.parent.mkdir(parents=True)
    daily.write_text(json.dumps({
        "schema_version": "1.0", "event_id": "evt_semantic", "date": "2026-08-13",
        "event_type": "observation", "status": "completed", "project": "fixture",
        "workstream": "semantic", "summary": "Semantic retrieval fixture about alpha evidence",
        "source": {
            "source_path": "docs/evidence.md", "heading": "Alpha evidence",
            "source_sha256": digest, "line_start": 1, "line_end": 1,
            "legacy_import": False, "requires_human_review": False,
        },
    }) + "\n", encoding="utf-8")
    ensure_lexical_index(root)


def _ranking_fixture(root: Path) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True)
    passage_source = docs / "passage.md"
    event_source = docs / "event.md"
    passage_source.write_text("needle appears only in this source passage\n", encoding="utf-8")
    event_source.write_text("unrelated source body\n", encoding="utf-8")
    passage_hash = hashlib.sha256(passage_source.read_bytes()).hexdigest()
    event_hash = hashlib.sha256(event_source.read_bytes()).hexdigest()
    events = root / "data/events"
    events.mkdir(parents=True)
    (events / "sources.jsonl").write_text(
        "\n".join([
            json.dumps({
                "source_id": "src_passage", "source_path": "docs/passage.md",
                "source_sha256": passage_hash, "source_type": "markdown", "legacy_import": False,
            }),
            json.dumps({
                "source_id": "src_event", "source_path": "docs/event.md",
                "source_sha256": event_hash, "source_type": "markdown", "legacy_import": False,
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    daily = events / "daily/2026-08-15/events.jsonl"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        "\n".join([
            json.dumps({
                "schema_version": "1.0", "event_id": "evt_passage", "date": "2026-08-14",
                "event_type": "observation", "status": "completed", "project": "fixture",
                "workstream": "ranking", "summary": "summary without the query term",
                "source": {
                    "source_path": "docs/passage.md", "heading": "Passage",
                    "source_sha256": passage_hash, "line_start": 1, "line_end": 1,
                    "legacy_import": False, "requires_human_review": False,
                },
            }),
            json.dumps({
                "schema_version": "1.0", "event_id": "evt_event", "date": "2026-08-15",
                "event_type": "observation", "status": "completed", "project": "fixture",
                "workstream": "ranking", "summary": "needle appears in the event summary",
                "source": {
                    "source_path": "docs/event.md", "heading": "Event",
                    "source_sha256": event_hash, "line_start": 1, "line_end": 1,
                    "legacy_import": False, "requires_human_review": False,
                },
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    ensure_lexical_index(root)


def test_demo_semantic_build_and_hybrid_search_preserve_evidence_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    _fixture(root)
    assert cli.main(["semantic", "configure", "--backend", "demo", "--root", str(root)]) == 0
    configured = json.loads(capsys.readouterr().out)
    assert configured["backend_class"] == "deterministic_demo"
    assert configured["trained_embedding_model"] is False
    assert cli.main(["semantic", "build", "--root", str(root)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["semantic"]["status"] == "current"
    assert built["remote_used"] is False

    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        semantic = server.memory_search_candidates("alpha evidence", mode="semantic")
        hybrid = server.memory_search_candidates("alpha evidence", mode="hybrid")
        lexical = server.memory_search_candidates("alpha evidence", mode="lexical")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)

    assert "semantic_backend" not in lexical["routing"]

    for response in (semantic, hybrid):
        backend = response["routing"]["semantic_backend"]
        assert backend["backend_class"] == "deterministic_demo"
        assert backend["trained_embedding_model"] is False
        assert backend["provider_id"] == "deterministic_demo"
        assert backend["model"] == "signed_hashing_v1"

    candidate = semantic["results"][0]
    assert semantic["candidate_only"] is True
    assert candidate["event_id"] == "evt_semantic"
    assert candidate["path"] == "docs/evidence.md"
    assert candidate["start_line"] == 1
    assert candidate["source_sha256"] == hashlib.sha256(
        (root / "docs/evidence.md").read_bytes()
    ).hexdigest()
    assert candidate["retrieval"]["semantic_rank"] == 1
    assert hybrid["results"][0]["retrieval"]["rrf_score"] > 0


def test_semantic_search_fails_closed_when_unconfigured_or_stale(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _fixture(root)
    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        with pytest.raises(RuntimeError, match="not configured"):
            server.memory_search_candidates("alpha", mode="semantic")
        cli.main(["semantic", "configure", "--backend", "demo", "--root", str(root)])
        cli.main(["semantic", "build", "--root", str(root)])
        (root / "docs/evidence.md").write_text("changed\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="stale or missing"):
            server.memory_search_candidates("alpha", mode="hybrid")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)


def test_adaptive_profile_routes_structural_queries_to_lexical_and_prose_to_semantic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    _fixture(root)
    profile = profile_template()
    profile["retrieval"] = {
        "mode": "adaptive",
        "semantic_backend": {"kind": "demo", "dimensions": 64, "auto_refresh": False},
    }
    write_profile(root, profile)
    assert cli.main(["semantic", "build", "--root", str(root)]) == 0
    capsys.readouterr()

    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        prose = server.memory_search_candidates("alpha research evidence")
        structural = server.memory_search_candidates("docs/evidence.md", mode="adaptive")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)

    assert prose["requested_mode"] == "configured"
    assert prose["mode"] == "semantic"
    assert prose["routing"]["selection_reason"] == "natural_language_semantic_route"
    assert prose["routing"]["configured_mode_reason"] == "configured_profile"
    assert structural["mode"] == "lexical"
    assert structural["routing"]["selection_reason"] == "structural_query_lexical_fast_path"
    assert structural["routing"]["semantic_attempted"] is False


def test_adaptive_profile_falls_back_to_lexical_when_semantic_is_not_built(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _fixture(root)
    profile = profile_template()
    profile["retrieval"] = {
        "mode": "adaptive",
        "semantic_backend": {"kind": "demo", "dimensions": 64, "auto_refresh": False},
    }
    write_profile(root, profile)

    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        fallback = server.memory_search_candidates("alpha research evidence")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)

    assert fallback["mode"] == "lexical"
    assert fallback["routing"]["selection_reason"] == "semantic_unavailable_lexical_fallback"
    assert fallback["routing"]["semantic_fallback"] is True


def test_adaptive_profile_falls_back_to_lexical_when_semantic_has_no_candidates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    _fixture(root)
    profile = profile_template()
    profile["retrieval"] = {
        "mode": "adaptive",
        "semantic_backend": {"kind": "demo", "dimensions": 64, "auto_refresh": False},
    }
    write_profile(root, profile)
    assert cli.main(["semantic", "build", "--root", str(root)]) == 0
    capsys.readouterr()

    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        fallback = server.memory_search_candidates("alpha evidence", status="not-present")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)

    assert fallback["mode"] == "lexical"
    assert fallback["routing"]["selection_reason"] == "semantic_empty_lexical_fallback"
    assert fallback["routing"]["semantic_fallback"] is True


def test_configured_mode_preserves_lexical_default_without_a_profile(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _fixture(root)

    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        result = server.memory_search_candidates("alpha evidence")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)

    assert result["requested_mode"] == "configured"
    assert result["mode"] == "lexical"
    assert result["routing"]["configured_mode_reason"] == "no_profile_legacy_default"


def test_event_first_backend_preserves_predecessor_event_summary_ranking(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _ranking_fixture(root)

    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        universal = server.memory_search_candidates(
            "needle", mode="lexical", candidate_backend="universal", top_k=2,
        )
        event_first = server.memory_search_candidates(
            "needle", mode="lexical", candidate_backend="event_first", top_k=2,
        )
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)

    assert universal["results"][0]["event_id"] == "evt_passage"
    assert event_first["results"][0]["event_id"] == "evt_event"
    assert event_first["routing"]["identity_gate"] == {
        "status": "passed",
        "checked_candidates": 1,
        "checked_locators": 1,
        "evidence_eligible_candidates": 1,
        "authority": "current_canonical_projection",
    }
    assert event_first["results"][0]["canonical_identity_verified"] is True


def test_candidate_identity_gate_rejects_a_tampered_backend_locator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "research"
    _ranking_fixture(root)
    original = server.search_event_first_lexical

    def tampered(query: str, top_k: int, status: str | None = None):
        candidates = original(query, top_k, status)
        candidates[0]["source_sha256"] = "0" * 64
        return candidates

    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        monkeypatch.setattr(server, "search_event_first_lexical", tampered)
        with pytest.raises(RuntimeError, match="canonical identity gate"):
            server.memory_search_candidates(
                "needle", mode="lexical", candidate_backend="event_first",
            )
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)


def test_candidate_identity_gate_rejects_tampered_nested_semantic_evidence(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _ranking_fixture(root)
    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        candidate = server.search_event_first_lexical("needle", 1)[0]
        candidate["retrieval"] = {
            "semantic_evidence": {
                "event_id": candidate["event_id"],
                "path": "docs/not-registered.md",
                "source_sha256": "0" * 64,
                "start_line": 1,
                "end_line": 1,
            },
        }
        with pytest.raises(RuntimeError, match="semantic evidence locator"):
            server._apply_candidate_identity_gate([candidate])
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)


def test_candidate_backend_rejects_an_unregistered_direct_value(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _ranking_fixture(root)
    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        with pytest.raises(ValueError, match="candidate backend"):
            server.memory_search_candidates("needle", candidate_backend="external_script")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)


def test_existing_positional_status_argument_remains_compatible(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _ranking_fixture(root)
    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        result = server.memory_search_candidates("needle", 2, "lexical", "completed")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)
    assert result["results"]
    assert all(candidate["status"] == "completed" for candidate in result["results"])


def test_profile_selects_event_first_hybrid_with_equal_weight_rrf(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    _fixture(root)
    profile = profile_template()
    profile["retrieval"] = {
        "mode": "hybrid",
        "candidate_backend": "event_first",
        "semantic_backend": {"kind": "demo", "dimensions": 64, "auto_refresh": False},
    }
    write_profile(root, profile)
    assert cli.main(["semantic", "build", "--root", str(root)]) == 0
    capsys.readouterr()

    previous_root, previous_db, previous_events = server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT
    try:
        server.configure_runtime(root)
        result = server.memory_search_candidates("alpha evidence")
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)

    assert result["candidate_backend"] == "event_first"
    assert result["mode"] == "hybrid"
    assert result["results"][0]["retrieval"]["rrf_weights"] == {
        "lexical": 1.0,
        "semantic": 1.0,
    }
    assert result["results"][0]["retrieval"]["semantic_evidence"][
        "canonical_identity_verified"
    ] is True
    assert result["routing"]["identity_gate"]["checked_locators"] == 2
    assert result["routing"]["identity_gate"]["status"] == "passed"
