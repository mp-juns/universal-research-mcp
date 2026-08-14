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
    finally:
        server.configure_runtime(previous_root, previous_db, previous_events)

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
