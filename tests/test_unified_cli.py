from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from universal_research_mcp.cli import build_parser, legacy_main, main
from universal_research_mcp.server import configure_runtime, memory_fetch_evidence, memory_search_candidates


def test_default_cli_surface_is_codex_only() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "provider" not in help_text
    assert "harness" in help_text
    assert "codex-agents" in help_text
    with pytest.raises(SystemExit):
        parser.parse_args(["provider", "status"])
    with pytest.raises(SystemExit):
        parser.parse_args(["agent", "status"])


def test_init_creates_queryable_empty_lexical_database_without_semantic_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    assert main(["init", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["lexical"]["status"] == "current"
    assert report["semantic"]["status"] == "missing"
    assert (root / "data/events/sources.jsonl").read_text(encoding="utf-8") == ""
    with sqlite3.connect(root / "data/index/research.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert not (root / "data/index/semantic.sqlite").exists()


def test_legacy_entrypoint_accepts_new_init_subcommand(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "legacy"
    assert legacy_main(["init", str(root)]) == 0
    capsys.readouterr()
    assert (root / "data/index/research.sqlite").is_file()


def test_host_input_cli_requires_registered_source_and_human_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    assert main(["init", str(root)]) == 0
    capsys.readouterr()
    source = root / "docs/note.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Note\n\nVerified input record.\n", encoding="utf-8")
    assert main([
        "source", "register", "docs/note.md", "--root", str(root),
        "--source-id", "src_note_v1", "--source-type", "markdown",
    ]) == 0
    source_report = json.loads(capsys.readouterr().out)
    source_sha = source_report["source"]["source_sha256"]
    with pytest.raises(ValueError, match="already registered"):
        main([
            "source", "register", "docs/note.md", "--root", str(root),
            "--source-id", "src_note_v2", "--source-type", "markdown",
        ])
    with pytest.raises(ValueError, match="escapes root"):
        main([
            "source", "register", "../escape.md", "--root", str(root),
            "--source-id", "src_escape", "--source-type", "markdown",
        ])
    approval = {
        "schema_version": "core/1.0", "record_id": "approval_write",
        "record_kind": "approval", "study_id": "study_demo",
        "occurred_at": "2026-08-12T01:00:00+00:00",
        "recorded_at": "2026-08-12T01:00:00+00:00", "status": "approved",
        "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
        "payload": {"scope": {"study_ids": ["study_demo"], "record_kinds": ["observation"]}},
    }
    approval_file = root / "approval.json"
    approval_file.write_text(json.dumps(approval), encoding="utf-8")
    assert main(["record", "approve", str(approval_file), "--root", str(root), "--confirm", "approval_write"]) == 0
    capsys.readouterr()
    record = {
        "schema_version": "core/1.0", "record_id": "observation_note",
        "record_kind": "observation", "study_id": "study_demo",
        "occurred_at": "2026-08-12T01:02:00+00:00",
        "recorded_at": "2026-08-12T01:03:00+00:00", "status": "completed",
        "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
        "approval_refs": ["approval_write"],
        "source_refs": [{
            "artifact_revision_id": f"artifact_note@sha256:{source_sha}",
            "locator": {"kind": "line_range", "path": "docs/note.md", "start": 1, "end": 3},
            "verification_status": "integrity_verified",
        }],
        "artifact_refs": ["artifact_note"], "payload": {"summary": "Verified input record"},
    }
    record_file = root / "record.json"
    record_file.write_text(json.dumps(record), encoding="utf-8")
    assert main(["record", "validate", str(record_file), "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert main(["record", "append", str(record_file), "--root", str(root), "--approval-ref", "approval_write"]) == 0
    capsys.readouterr()
    assert (root / "data/events/daily/2026-08-12/events.jsonl").is_file()
    configure_runtime(root)
    assert memory_search_candidates("Verified")["results"][0]["event_id"] == "observation_note"
    source.write_text("# Changed\n", encoding="utf-8")
    withheld = memory_fetch_evidence("docs/note.md", 1, 3, 0, event_id="observation_note")
    assert withheld["integrity_status"] == "mismatched"
    assert withheld["content_withheld"] is True
    assert "content" not in withheld
    assert "Changed" in memory_fetch_evidence(
        "docs/note.md", 1, 3, 0, event_id="observation_note",
        allow_mismatched_content=True,
    )["content"]


def test_stale_lexical_index_blocks_mcp_candidate_search(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    assert main(["init", str(root)]) == 0
    capsys.readouterr()
    daily = root / "data/events/daily/2026-08-12/events.jsonl"
    daily.parent.mkdir(parents=True)
    daily.write_text(json.dumps({
        "event_id": "evt_stale", "date": "2026-08-12", "event_type": "observation",
        "status": "completed", "project": "fixture", "summary": "stale event",
    }) + "\n", encoding="utf-8")
    configure_runtime(root)
    with pytest.raises(RuntimeError, match="stale"):
        memory_search_candidates("stale")


def test_legacy_harness_run_is_fail_closed_before_packet_or_provider_access(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([
        "harness", "run", str(tmp_path / "not-read.json"), "--root", str(tmp_path),
    ]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    assert report["executed"] is False
    assert report["reason"] == "explicit_execution_confirmation_missing"


