from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from universal_research_mcp import server
from universal_research_mcp.cli import main
from universal_research_mcp.core import ingest as ingest_module
from universal_research_mcp.core.canonical_io import canonical_write_lock
from universal_research_mcp.core.input import all_records, append_record, register_source
from universal_research_mcp.indexing import initialize_project
from universal_research_mcp.runtime import ProjectPaths
from universal_research_mcp.runtime.ingest_approval import IngestApprovalStore
from universal_research_mcp.runtime.semantic_config import configure_demo
from universal_research_mcp.runtime import project_io


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


def test_mcp_ingest_indexes_and_fetches_every_source_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        state_root = tmp_path / "host-state"
        monkeypatch.setenv(
            "UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(state_root)
        )
        initialize_project(root)
        append_record(root, _approval(), approval_bootstrap=True)
        configure_demo(root, auto_refresh=True)

        code = root / "src/release_gate.py"
        build = root / "pyproject.toml"
        code.parent.mkdir(parents=True)
        code.write_text(
            "def release_gate() -> str:\n    return 'code-source-token'\n",
            encoding="utf-8",
        )
        build.write_text(
            "[project]\nname = \"fixture\"\nbuild-token = \"verified\"\n",
            encoding="utf-8",
        )
        code_hash = _sha256(code)
        build_hash = _sha256(build)
        record = {
            **_record("src/release_gate.py", code_hash),
            "record_id": "observation_code_build",
            "source_refs": [
                {
                    "artifact_revision_id": f"artifact_code@sha256:{code_hash}",
                    "locator": {
                        "kind": "line_range",
                        "path": "src/release_gate.py",
                        "start": 1,
                        "end": 2,
                        "heading": "Release gate code",
                    },
                    "verification_status": "integrity_verified",
                },
                {
                    "artifact_revision_id": f"artifact_build@sha256:{build_hash}",
                    "locator": {
                        "kind": "line_range",
                        "path": "pyproject.toml",
                        "start": 1,
                        "end": 3,
                        "heading": "Build configuration",
                    },
                    "verification_status": "integrity_verified",
                },
            ],
            "artifact_refs": ["artifact_code", "artifact_build"],
            "payload": {"summary": "Code and build files were ingested together."},
        }
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record,
            "approval_ingest",
            [
                {
                    "path": "src/release_gate.py",
                    "source_id": "src_release_gate",
                    "source_type": "python",
                },
                {
                    "path": "pyproject.toml",
                    "source_id": "src_pyproject",
                    "source_type": "toml",
                },
            ],
        )
        receipt = _receipt(root, prepared, state_root)
        committed = server.research_commit_ingest(
            prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"]
        )

        assert committed["derived_refresh"]["lexical"]["status"] == "current"
        assert committed["derived_refresh"]["semantic"]["status"] == "current"
        with sqlite3.connect(root / "data/index/research.sqlite") as db:
            projected = db.execute(
                "SELECT source_path, source_sha256 FROM event_sources "
                "WHERE event_id = ? ORDER BY source_ordinal",
                ("observation_code_build",),
            ).fetchall()
            passages = db.execute(
                "SELECT source_path FROM source_passage_fts "
                "WHERE event_id = ? ORDER BY source_path",
                ("observation_code_build",),
            ).fetchall()
        assert projected == [
            ("src/release_gate.py", code_hash),
            ("pyproject.toml", build_hash),
        ]
        assert passages == [("pyproject.toml",), ("src/release_gate.py",)]

        for path, end, digest, token in (
            ("src/release_gate.py", 2, code_hash, "code-source-token"),
            ("pyproject.toml", 3, build_hash, "build-token"),
        ):
            evidence = server.memory_fetch_evidence(
                path,
                1,
                end,
                0,
                event_id="observation_code_build",
                expected_sha256=digest,
            )
            assert evidence["integrity_status"] == "matched"
            assert token in evidence["content"]
            assert server.indexed_source_hashes(
                path, "observation_code_build"
            ) == [digest]

        with sqlite3.connect(root / "data/index/semantic.sqlite") as db:
            semantic_paths = db.execute(
                "SELECT source_path FROM passage_embeddings "
                "WHERE event_id = ? ORDER BY source_path",
                ("observation_code_build",),
            ).fetchall()
        assert semantic_paths == [("pyproject.toml",), ("src/release_gate.py",)]
        assert server.memory_search_candidates(
            "build-token", mode="lexical"
        )["results"][0]["path"] == "pyproject.toml"
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


def test_mcp_ingest_resumes_after_one_canonical_file_was_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        state_root = tmp_path / "host-state"
        monkeypatch.setenv("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(state_root))
        _source, record = _prepared_project(root)
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        receipt = _receipt(root, prepared, state_root)
        original = ingest_module._apply_transaction_operation
        calls = 0

        def fail_second(paths: ProjectPaths, operation: dict[str, object]) -> str:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected event-ledger write failure")
            return original(paths, operation)

        monkeypatch.setattr(ingest_module, "_apply_transaction_operation", fail_second)
        with pytest.raises(OSError, match="injected event-ledger write failure"):
            server.research_commit_ingest(
                prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
            )
        status = server.research_pending_ingest_status(prepared["draft_id"])
        assert status["status"] == "recovery_required"
        assert status["applied_operation_count"] == 1
        assert len(all_records(ProjectPaths.from_root(root))) == 1
        source_lines = (root / "data/events/sources.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(source_lines) == 1

        monkeypatch.setattr(ingest_module, "_apply_transaction_operation", original)
        committed = server.research_commit_ingest(
            prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
        )
        assert committed["status"] == "committed"
        assert committed["approval_receipt"]["resumed"] is True
        assert len(all_records(ProjectPaths.from_root(root))) == 2
        assert len((root / "data/events/sources.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    finally:
        server.configure_runtime(*prior)


def test_mcp_ingest_resumes_after_canonical_commit_before_consumption_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        state_root = tmp_path / "host-state"
        monkeypatch.setenv("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(state_root))
        _source, record = _prepared_project(root)
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        receipt = _receipt(root, prepared, state_root)
        original = ingest_module._create_only_json

        def fail_consumption(paths: ProjectPaths, path: Path, payload: dict[str, object]) -> None:
            if path.parent.name == "consumed":
                raise OSError("injected consumption-marker failure")
            original(paths, path, payload)

        monkeypatch.setattr(ingest_module, "_create_only_json", fail_consumption)
        with pytest.raises(OSError, match="injected consumption-marker failure"):
            server.research_commit_ingest(
                prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
            )
        status = server.research_pending_ingest_status(prepared["draft_id"])
        assert status["status"] == "recovery_required"
        assert status["applied_operation_count"] == 2
        assert len(all_records(ProjectPaths.from_root(root))) == 2

        monkeypatch.setattr(ingest_module, "_create_only_json", original)
        committed = server.research_commit_ingest(
            prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
        )
        assert committed["status"] == "committed"
        assert committed["approval_receipt"]["resumed"] is True
        assert len(all_records(ProjectPaths.from_root(root))) == 2
        assert len((root / "data/events/sources.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    finally:
        server.configure_runtime(*prior)


def test_registration_refuses_reserved_secret_names_at_word_boundaries(tmp_path: Path) -> None:
    """A registered source must stay fetchable: deny reserved names up front."""

    root = tmp_path / "research"
    _source, record = _prepared_project(root)
    (root / "docs/tokenizer-notes.md").write_text("Vocabulary size was 32000.\n", encoding="utf-8")
    (root / "docs/auth_token.json").write_text("{}\n", encoding="utf-8")

    registered = register_source(
        root, "docs/tokenizer-notes.md", source_id="src_tokenizer", source_type="markdown",
    )
    assert registered["source_path"] == "docs/tokenizer-notes.md"

    with pytest.raises(ValueError, match="cannot be registered"):
        register_source(root, "docs/auth_token.json", source_id="src_auth", source_type="json")

    with pytest.raises(ValueError, match="cannot be registered"):
        ingest_module.prepare_ingest(
            root, record=record, approval_ref="approval_ingest",
            source_registrations=[
                {"path": "docs/auth_token.json", "source_id": "src_auth", "source_type": "json"},
            ],
        )


def test_mcp_ingest_groups_multiple_source_registrations_in_one_atomic_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        state_root = tmp_path / "host-state"
        monkeypatch.setenv("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(state_root))
        _source, record = _prepared_project(root)
        second = root / "docs/second.md"
        second.write_text("# Second source\n", encoding="utf-8")
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record,
            "approval_ingest",
            [
                {"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"},
                {"path": "docs/second.md", "source_id": "src_second", "source_type": "markdown"},
            ],
        )
        receipt = _receipt(root, prepared, state_root)
        committed = server.research_commit_ingest(
            prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
        )
        assert committed["status"] == "committed"
        assert len(committed["registered_sources"]) == 2
        assert len((root / "data/events/sources.jsonl").read_text(encoding="utf-8").splitlines()) == 2
        transaction = json.loads(next(
            (root / "data/ingest-drafts/transactions").glob("*.json")
        ).read_text(encoding="utf-8"))
        assert [item["kind"] for item in transaction["operations"]] == [
            "source_registration", "event_record",
        ]
    finally:
        server.configure_runtime(*prior)


def test_mcp_ingest_reports_success_if_only_final_journal_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root = tmp_path / "research"
        state_root = tmp_path / "host-state"
        monkeypatch.setenv("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT", str(state_root))
        _source, record = _prepared_project(root)
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        receipt = _receipt(root, prepared, state_root)
        original = ingest_module._store_transaction
        calls = 0

        def fail_final(paths: ProjectPaths, transaction: dict[str, object]) -> None:
            nonlocal calls
            calls += 1
            if transaction.get("status") == "committed":
                raise OSError("injected final journal write failure")
            original(paths, transaction)

        monkeypatch.setattr(ingest_module, "_store_transaction", fail_final)
        committed = server.research_commit_ingest(
            prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
        )
        assert calls >= 5
        assert committed["status"] == "committed"
        assert committed["transaction_status"] == "committed_journal_finalization_pending"
        assert committed["transaction_journal_warning"]["error_type"] == "OSError"
        assert len(all_records(ProjectPaths.from_root(root))) == 2
        assert server.research_pending_ingest_status(prepared["draft_id"])["status"] == "consumed"
        with pytest.raises(ValueError, match="already consumed"):
            server.research_commit_ingest(
                prepared["draft_id"], prepared["draft_sha256"], receipt["receipt_id"],
            )
    finally:
        server.configure_runtime(*prior)


def _make_directory_link(path: Path, destination: Path) -> None:
    if path.exists():
        path.rename(path.with_name(path.name + ".original"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(destination, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")


@pytest.mark.parametrize("relative", [
    "data/ingest-drafts", "data/ingest-drafts/pending",
    "data/ingest-drafts/consumed", "data/ingest-drafts/transactions", "data/audit",
])
@pytest.mark.parametrize("phase", ["prepare", "commit"])
@pytest.mark.parametrize("outside_exists", [True, False])
def test_ingest_rejects_parent_links_before_writing(
    tmp_path: Path, relative: str, phase: str, outside_exists: bool,
) -> None:
    root = tmp_path / "research"
    outside = tmp_path / "outside"
    _source, record = _prepared_project(root)
    registrations = [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}]
    prepared = ingest_module.prepare_ingest(
        root, record=record, approval_ref="approval_ingest", source_registrations=registrations,
    ) if phase == "commit" else None
    if outside_exists:
        outside.mkdir()
    ledger = root / "data/events/daily/2026-08-13/events.jsonl"
    before = ledger.read_bytes()
    _make_directory_link(root / relative, outside)

    with pytest.raises(ValueError, match="symlink|reparse"):
        if prepared is None:
            ingest_module.prepare_ingest(
                root, record=record, approval_ref="approval_ingest", source_registrations=registrations,
            )
        else:
            ingest_module.commit_ingest(
                root, draft_id=prepared["draft_id"], draft_sha256=prepared["draft_sha256"],
                approval_receipt_id="receipt_" + "0" * 24,
            )
    assert ledger.read_bytes() == before
    assert outside.exists() is outside_exists
    if outside_exists:
        assert list(outside.iterdir()) == []


@pytest.mark.parametrize("link_type", ["symlink", "hardlink"])
@pytest.mark.parametrize("artifact", ["pending", "transactions", "consumed", "audit", "lock"])
def test_ingest_rejects_linked_metadata_files(
    tmp_path: Path, artifact: str, link_type: str,
) -> None:
    root = tmp_path / "research"
    _prepared_project(root)
    paths = ProjectPaths.from_root(root)
    outside = tmp_path / "outside.jsonl"
    original = b"external file must remain unchanged\n"
    outside.write_bytes(original)
    if artifact == "audit":
        target = root / "data/audit/ingest-events.jsonl"
    elif artifact == "lock":
        target = root / "data/ingest-drafts/pending/.commit.lock"
    else:
        target = root / "data/ingest-drafts" / artifact / ("ingest_" + "a" * 24 + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if link_type == "symlink":
            target.symlink_to(outside)
        else:
            os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"{link_type} is unavailable: {exc}")

    with pytest.raises(ValueError, match="symlink|reparse|single-link"):
        if artifact == "audit":
            ingest_module._append_audit(paths, {"event_type": "fixture"})
        elif artifact == "lock":
            with canonical_write_lock(paths):
                pytest.fail("a linked lock must not be acquired")
        elif artifact == "transactions":
            ingest_module._replace_json(paths, target, {"status": "fixture"})
        else:
            ingest_module._create_only_json(paths, target, {"status": "fixture"})
    assert outside.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX no-follow directory descriptors")
def test_ingest_rejects_a_parent_swapped_after_path_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "research"
    _prepared_project(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    pending = root / "data/ingest-drafts/pending"
    original_open = os.open
    swapped = False

    def replace_parent(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == "pending" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            _make_directory_link(pending, outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(project_io.os, "open", replace_parent)
    with pytest.raises((ValueError, OSError)):
        ingest_module._create_only_json(
            ProjectPaths.from_root(root), pending / "fixture.json", {"fixture": True},
        )
    assert swapped
    assert list(outside.iterdir()) == []


def test_portable_path_rechecks_parent_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "research"
    _prepared_project(root)
    monkeypatch.setattr(project_io, "_USE_DIRECTORY_FDS", False)
    files = project_io.ProjectFiles(root)
    target = root / "data/audit/fixture.json"
    files.create(target, b"original\n")
    files.replace(target, b"updated\n", expected=b"original\n", check_expected=True)
    assert files.read(target) == b"updated\n"
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_directory_link(target.parent, outside)
    with pytest.raises(ValueError, match="symlink|reparse"):
        files.replace(target, b"must not escape\n")
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows junction creation")
@pytest.mark.parametrize("relative", [
    "data/ingest-drafts", "data/ingest-drafts/pending",
    "data/ingest-drafts/consumed", "data/ingest-drafts/transactions", "data/audit",
])
@pytest.mark.parametrize("phase", ["prepare", "commit"])
def test_ingest_rejects_windows_junction(tmp_path: Path, relative: str, phase: str) -> None:
    root = tmp_path / "research"
    _source, record = _prepared_project(root)
    registrations = [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}]
    prepared = ingest_module.prepare_ingest(
        root, record=record, approval_ref="approval_ingest", source_registrations=registrations,
    ) if phase == "commit" else None
    target = root / relative
    if target.exists():
        target.rename(target.with_name(target.name + ".original"))
    target.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    ledger = root / "data/events/daily/2026-08-13/events.jsonl"
    before = ledger.read_bytes()
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(target), str(outside)],
        check=True, capture_output=True, timeout=10,
    )
    with pytest.raises(ValueError, match="symlink|reparse"):
        if prepared is None:
            ingest_module.prepare_ingest(
                root, record=record, approval_ref="approval_ingest", source_registrations=registrations,
            )
        else:
            ingest_module.commit_ingest(
                root, draft_id=prepared["draft_id"], draft_sha256=prepared["draft_sha256"],
                approval_receipt_id="receipt_" + "0" * 24,
            )
    assert ledger.read_bytes() == before
    assert list(outside.iterdir()) == []


def _paused_administrator_writer(root: str, connection: Connection) -> None:
    """A real separate writer process paused between validation and append."""
    from universal_research_mcp.core import input as input_module

    original_validate = input_module.validate_candidate_records

    def paused_validate(*args: object, **kwargs: object) -> object:
        result = original_validate(*args, **kwargs)
        connection.send("validated")
        if not connection.poll(20) or connection.recv() != "continue":
            raise RuntimeError("test writer was not released")
        return result

    input_module.validate_candidate_records = paused_validate
    try:
        input_module.append_record(
            root, {**_approval(), "record_id": "approval_concurrent"}, approval_bootstrap=True,
        )
        connection.send("committed")
    except Exception as exc:
        connection.send(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        connection.close()


def test_cli_and_mcp_writers_share_a_process_lock_across_validation(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _prepared_project(root)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_paused_administrator_writer, args=(str(root), child))
    process.start()
    child.close()
    try:
        assert parent.poll(20), "writer did not reach validation"
        assert parent.recv() == "validated"
        with pytest.raises(RuntimeError, match="canonical write lock"):
            append_record(root, {**_approval(), "record_id": "approval_concurrent"}, approval_bootstrap=True)
        with pytest.raises(RuntimeError, match="canonical write lock"):
            register_source(root, "docs/ingest.md", source_id="src_concurrent", source_type="markdown")
        with pytest.raises(RuntimeError, match="canonical write lock"):
            ingest_module.commit_ingest(
                root, draft_id="ingest_" + "0" * 24, draft_sha256="0" * 64,
                approval_receipt_id="receipt_" + "0" * 24,
            )
        parent.send("continue")
        assert parent.poll(20), "writer did not complete"
        assert parent.recv() == "committed"
        process.join(10)
        assert process.exitcode == 0
        with pytest.raises(ValueError, match="record ID already exists"):
            append_record(root, {**_approval(), "record_id": "approval_concurrent"}, approval_bootstrap=True)
        assert len(all_records(ProjectPaths.from_root(root))) == 2
        assert (root / "data/events/sources.jsonl").read_bytes() == b""
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
        parent.close()


@pytest.mark.parametrize("writer", ["record", "source"])
def test_admin_append_fsyncs_staging_and_preserves_original_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: str,
) -> None:
    root = tmp_path / "research"
    _prepared_project(root)
    target = root / (
        "data/events/sources.jsonl" if writer == "source"
        else "data/events/daily/2026-08-13/events.jsonl"
    )
    before = target.read_bytes()
    flushed: set[tuple[int, int]] = set()
    original_fsync = os.fsync
    original_replace = os.replace

    def track_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        flushed.add((metadata.st_dev, metadata.st_ino))
        original_fsync(descriptor)

    def fail_replace(source: object, destination: object, **kwargs: object) -> None:
        if Path(destination).name == target.name:
            metadata = os.stat(source, dir_fd=kwargs.get("src_dir_fd"), follow_symlinks=False)
            assert (metadata.st_dev, metadata.st_ino) in flushed
            raise OSError("injected atomic replacement failure")
        original_replace(source, destination, **kwargs)

    def append() -> None:
        if writer == "source":
            register_source(root, "docs/ingest.md", source_id="src_durable", source_type="markdown")
        else:
            append_record(root, {**_approval(), "record_id": "approval_durable"}, approval_bootstrap=True)

    monkeypatch.setattr(project_io.os, "fsync", track_fsync)
    monkeypatch.setattr(project_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected atomic replacement failure"):
        append()
    assert target.read_bytes() == before
    assert list(target.parent.glob(".*.tmp")) == []
    assert not ProjectPaths.from_root(root).canonical_lock.exists()
    monkeypatch.setattr(project_io.os, "replace", original_replace)
    append()
    assert target.read_bytes().startswith(before)
    assert len(target.read_bytes().splitlines()) == len(before.splitlines()) + 1


@pytest.mark.parametrize("writer", ["record", "source"])
@pytest.mark.parametrize("link_type", ["file_symlink", "parent_symlink", "hardlink"])
def test_admin_writes_reject_linked_canonical_paths(
    tmp_path: Path, writer: str, link_type: str,
) -> None:
    root = tmp_path / "research"
    _prepared_project(root)
    target = root / (
        "data/events/sources.jsonl" if writer == "source"
        else "data/events/daily/2026-08-13/events.jsonl"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / target.name
    original = target.read_bytes()
    external.write_bytes(original)
    if link_type == "parent_symlink":
        _make_directory_link(target.parent, outside)
    else:
        target.unlink()
        try:
            if link_type == "file_symlink":
                target.symlink_to(external)
            else:
                os.link(external, target)
        except OSError as exc:
            pytest.skip(f"{link_type} is unavailable: {exc}")
    with pytest.raises(ValueError, match="symlink|reparse|single-link|escapes root"):
        if writer == "source":
            register_source(root, "docs/ingest.md", source_id="src_linked", source_type="markdown")
        else:
            append_record(root, {**_approval(), "record_id": "approval_linked"}, approval_bootstrap=True)
    assert external.read_bytes() == original
    assert list(outside.iterdir()) == [external]
