from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from universal_research_mcp import server
from universal_research_mcp.cli import main
from universal_research_mcp.core.input import all_records, append_record
from universal_research_mcp.indexing import initialize_project
from universal_research_mcp.runtime import ProjectPaths
from universal_research_mcp.runtime.ingest_approval import IngestApprovalStore
from universal_research_mcp.runtime.semantic_config import configure_demo


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approval() -> dict[str, object]:
    return {
        "schema_version": "core/1.0", "record_id": "approval_ingest",
        "record_kind": "approval", "study_id": "study_ingest",
        "occurred_at": "2026-08-13T00:00:00+00:00",
        "recorded_at": "2026-08-13T00:00:00+00:00", "status": "approved",
        "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
        "payload": {"scope": {"study_ids": ["study_ingest"], "record_kinds": ["observation"]}},
    }


def _record(path: str, digest: str, *, record_id: str = "observation_ingest") -> dict[str, object]:
    return {
        "schema_version": "core/1.0", "record_id": record_id,
        "record_kind": "observation", "study_id": "study_ingest",
        "occurred_at": "2026-08-13T00:01:00+00:00",
        "recorded_at": "2026-08-13T00:02:00+00:00", "status": "completed",
        "created_by": {"actor_id": "actor_researcher", "actor_type": "ai"},
        "approval_refs": ["approval_ingest"],
        "source_refs": [{
            "artifact_revision_id": f"artifact_ingest@sha256:{digest}",
            "locator": {"kind": "line_range", "path": path, "start": 1, "end": 2},
            "verification_status": "integrity_verified",
        }],
        "artifact_refs": ["artifact_ingest"],
        "payload": {"summary": "A bounded MCP ingestion fixture."},
    }


def _prepared_project(root: Path) -> tuple[Path, dict[str, object]]:
    initialize_project(root)
    append_record(root, _approval(), approval_bootstrap=True)
    configure_demo(root, auto_refresh=True)
    source = root / "docs/ingest.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Ingest fixture\n\nA source-bound observation.\n", encoding="utf-8")
    return source, _record("docs/ingest.md", _sha256(source))


def _receipt(root: Path, prepared: dict[str, object], state_root: Path) -> dict[str, object]:
    return IngestApprovalStore(root, state_root=state_root).issue(
        draft_id=str(prepared["draft_id"]),
        draft_sha256=str(prepared["draft_sha256"]),
        expires_at="2030-01-01T00:00:00+00:00",
    )


def test_mcp_ingest_commits_exact_draft_and_refreshes_derived_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        monkeypatch.setenv("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(tmp_path / "host-state"))
        source, record = _prepared_project(root)
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        assert prepared["status"] == "prepared"
        assert prepared["canonical_append"] is False
        assert len(all_records(ProjectPaths.from_root(root))) == 1
        pending = server.research_pending_ingest_status(prepared["draft_id"])
        assert pending["status"] == "pending"
        assert "record" not in pending

        receipt = _receipt(root, prepared, tmp_path / "host-state")
        committed = server.research_commit_ingest(
            prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
        )
        assert committed["status"] == "committed"
        assert committed["canonical_append"] is True
        assert committed["registered_sources"][0]["source_sha256"] == _sha256(source)
        assert committed["derived_refresh"]["lexical"]["status"] == "current"
        assert committed["derived_refresh"]["semantic"]["status"] == "current"
        assert len(all_records(ProjectPaths.from_root(root))) == 2
        assert server.memory_search_candidates("bounded MCP ingestion")["results"][0]["event_id"] == "observation_ingest"
        with pytest.raises(ValueError, match="already consumed"):
            server.research_commit_ingest(
                prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
            )
    finally:
        server.configure_runtime(*prior)


def test_mcp_ingest_refuses_substitution_source_or_canonical_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        monkeypatch.setenv("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(tmp_path / "host-state"))
        source, record = _prepared_project(root)
        server.configure_runtime(root)
        missing_approval = {**record, "approval_refs": ["approval_missing"]}
        with pytest.raises(ValueError, match="referenced approval record does not exist"):
            server.research_prepare_ingest(missing_approval, "approval_missing")

        source_changed = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        receipt = _receipt(root, source_changed, tmp_path / "host-state")
        with pytest.raises(ValueError, match="draft_sha256"):
            server.research_commit_ingest(source_changed["draft_id"], "0" * 64, receipt["receipt_id"])
        source.write_text("# Replaced fixture\n", encoding="utf-8")
        with pytest.raises(ValueError, match="source content changed"):
            server.research_commit_ingest(
                source_changed["draft_id"], source_changed["draft_sha256"], receipt["receipt_id"],
            )

        source.write_text("# Ingest fixture\n\nA source-bound observation.\n", encoding="utf-8")
        canonical_changed = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest_2", "source_type": "markdown"}],
        )
        second_receipt = _receipt(root, canonical_changed, tmp_path / "host-state")
        append_record(root, {
            **_approval(), "record_id": "approval_other",
            "recorded_at": "2026-08-13T00:03:00+00:00",
        }, approval_bootstrap=True)
        with pytest.raises(ValueError, match="canonical ledger changed"):
            server.research_commit_ingest(
                canonical_changed["draft_id"], canonical_changed["draft_sha256"], second_receipt["receipt_id"],
            )
    finally:
        server.configure_runtime(*prior)


def test_mcp_ingest_tools_are_declared_as_mutating(tmp_path: Path) -> None:
    del tmp_path
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name in ("research_prepare_ingest", "research_commit_ingest"):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.idempotentHint is False
        assert tools[name].annotations.openWorldHint is False
    assert tools["research_pending_ingest_status"].annotations.readOnlyHint is True


def test_mcp_ingest_refuses_a_concurrent_commit_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        monkeypatch.setenv("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(tmp_path / "host-state"))
        _source, record = _prepared_project(root)
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        lock = root / "data/ingest-drafts/pending/.commit.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("active fixture lock\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="another ingest commit is active"):
            server.research_commit_ingest(
                prepared["draft_id"], prepared["draft_sha256"], "receipt_" + "0" * 24,
            )
        assert server.research_pending_ingest_status(prepared["draft_id"])["status"] == "pending"
    finally:
        server.configure_runtime(*prior)


def test_mcp_ingest_requires_signed_external_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        monkeypatch.setenv("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(tmp_path / "host-state"))
        _source, record = _prepared_project(root)
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        with pytest.raises(RuntimeError, match="receipt is missing"):
            server.research_commit_ingest(
                prepared["draft_id"], prepared["draft_sha256"], "receipt_" + "0" * 24,
            )
        receipt = _receipt(root, prepared, tmp_path / "host-state")
        receipt_path = IngestApprovalStore(root, state_root=tmp_path / "host-state").receipt_path(receipt["receipt_id"])
        receipt_path.write_text("{}\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="receipt schema is invalid"):
            server.research_commit_ingest(
                prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
            )
    finally:
        server.configure_runtime(*prior)


def test_ingest_approval_cli_issues_exact_external_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        _source, record = _prepared_project(root)
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        state_root = tmp_path / "host-state"
        assert main([
            "ingest", "approve", "--root", str(root), "--draft-id", prepared["draft_id"],
            "--draft-sha256", prepared["draft_sha256"],
            "--confirm-draft-sha256", prepared["draft_sha256"],
            "--expires-at", "2030-01-01T00:00:00+00:00", "--state-root", str(state_root),
        ]) == 0
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["draft_id"] == prepared["draft_id"]
        assert receipt["private_key_exposed"] is False
        with pytest.raises(ValueError, match="confirm-draft-sha256"):
            main([
            "ingest", "approve", "--root", str(root), "--draft-id", prepared["draft_id"],
            "--draft-sha256", prepared["draft_sha256"],
            "--confirm-draft-sha256", "0" * 64,
            "--expires-at", "2030-01-01T00:00:00+00:00", "--state-root", str(state_root),
            ])
    finally:
        server.configure_runtime(*prior)
