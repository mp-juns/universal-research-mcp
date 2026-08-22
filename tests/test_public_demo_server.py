from __future__ import annotations

from argparse import Namespace
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from universal_research_mcp import server
from universal_research_mcp.cli import main as cli_main
from universal_research_mcp.core.input import append_record, register_source
from universal_research_mcp.indexing.lexical import ensure_lexical_index, initialize_project
from universal_research_mcp.public_demo import (
    PUBLIC_CONFIRMATION,
    build_manifest,
    validate_manifest,
    write_manifest,
)
from universal_research_mcp.runtime.semantic_config import configure_local


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(root: Path) -> Path:
    initialize_project(root)
    source = root / "public/evidence.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Public evidence\n\nA reviewed public statement.\n", encoding="utf-8")
    register_source(root, "public/evidence.md", source_id="src_public_evidence", source_type="markdown")
    append_record(root, {
        "schema_version": "core/1.0",
        "record_id": "approval_public_demo",
        "record_kind": "approval",
        "study_id": "study_public_demo",
        "occurred_at": "2026-08-15T00:00:00+00:00",
        "recorded_at": "2026-08-15T00:00:00+00:00",
        "status": "approved",
        "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
        "payload": {"scope": {"study_ids": ["study_public_demo"], "record_kinds": ["observation"]}},
    }, approval_bootstrap=True)
    digest = _sha256(source)
    append_record(root, {
        "schema_version": "core/1.0",
        "record_id": "observation_public_demo",
        "record_kind": "observation",
        "study_id": "study_public_demo",
        "occurred_at": "2026-08-15T00:01:00+00:00",
        "recorded_at": "2026-08-15T00:01:00+00:00",
        "status": "completed",
        "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
        "approval_refs": ["approval_public_demo"],
        "source_refs": [{
            "artifact_revision_id": f"artifact_public@sha256:{digest}",
            "locator": {"kind": "line_range", "path": "public/evidence.md", "start": 1, "end": 3},
            "verification_status": "integrity_verified",
        }],
        "artifact_refs": ["artifact_public"],
        "payload": {"summary": "A reviewed public demo observation."},
    }, approval_ref="approval_public_demo")
    ensure_lexical_index(root)
    return source


def test_public_manifest_binds_ledger_sources_and_index(tmp_path: Path) -> None:
    root = tmp_path / "research"
    source = _project(root)
    document = build_manifest(
        root,
        corpus_id="public-fixture",
        display_name="Public Fixture",
        confirmation=PUBLIC_CONFIRMATION,
    )
    prepared = write_manifest(root, document)
    verified = validate_manifest(root)

    assert prepared["server_started"] is False
    assert verified["status"] == "verified"
    assert verified["canonical_write_disabled"] is True
    assert verified["event_count"] == 2
    assert verified["source_file_count"] == 1

    source.write_text("changed after review\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed after review"):
        validate_manifest(root)


def test_public_demo_cli_prepares_then_verifies_without_serving(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    _project(root)
    assert cli_main([
        "public-demo", "prepare", "--root", str(root),
        "--corpus-id", "public-fixture", "--display-name", "Public Fixture",
        "--confirm-public-data", PUBLIC_CONFIRMATION,
    ]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["status"] == "prepared"
    assert prepared["server_started"] is False
    assert cli_main(["public-demo", "verify", "--root", str(root)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"


def test_public_manifest_requires_exact_disclosure_confirmation(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _project(root)
    with pytest.raises(ValueError, match="confirmation must exactly equal"):
        build_manifest(
            root, corpus_id="public-fixture", display_name="Public Fixture", confirmation="yes",
        )


def test_public_manifest_rejects_unpinned_local_model_backend(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _project(root)
    model = tmp_path / "local-model"
    model.mkdir()
    configure_local(root, model_path=model, trust_local_model_code=False)
    with pytest.raises(ValueError, match="model-snapshot manifest"):
        build_manifest(
            root,
            corpus_id="public-fixture",
            display_name="Public Fixture",
            confirmation=PUBLIC_CONFIRMATION,
        )


def test_public_surface_is_allowlist_only() -> None:
    names = [*server.PUBLIC_DEMO_TOOL_NAMES, "research_commit_ingest", "future_unknown_tool"]
    manager = SimpleNamespace(list_tools=lambda: [SimpleNamespace(name=name) for name in names])
    removed: list[str] = []
    low_level = SimpleNamespace(instructions="private")
    fake_mcp = SimpleNamespace(
        _tool_manager=manager,
        _mcp_server=low_level,
        remove_tool=removed.append,
    )
    with patch.object(server, "mcp", fake_mcp):
        server._restrict_public_tool_surface()
    assert set(removed) == {"research_commit_ingest", "future_unknown_tool"}
    assert low_level.instructions == server.PUBLIC_DEMO_INSTRUCTIONS
    assert "research_prepare_ingest" not in low_level.instructions


def test_public_tools_declare_read_only_annotations() -> None:
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name in server.PUBLIC_DEMO_TOOL_NAMES:
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False
        assert tools[name].annotations.openWorldHint is False


def test_public_mode_blocks_python_level_ingest_bypass() -> None:
    previous = server.PUBLIC_DEMO_STATE
    server.PUBLIC_DEMO_STATE = {"enabled": True, "status": "verified"}
    try:
        with pytest.raises(PermissionError, match="disabled in public demo mode"):
            server.research_prepare_ingest({}, "approval_public")
        with pytest.raises(PermissionError, match="disabled in public demo mode"):
            server.research_commit_ingest("draft", "0" * 64, "receipt")
        with pytest.raises(PermissionError, match="disabled in public demo mode"):
            server.research_pending_ingest_status("draft")
    finally:
        server.PUBLIC_DEMO_STATE = previous


def test_public_http_requires_explicit_host_allowlist_off_loopback() -> None:
    parsed = server.parse_args([
        "--root", "fixture", "--transport", "streamable-http", "--public-demo",
        "--host", "0.0.0.0",
    ])
    with pytest.raises(ValueError, match="requires at least one --allowed-host"):
        server._configure_public_transport(parsed)

    allowed = Namespace(**{**vars(parsed), "allowed_host": ["research.example.org"]})
    prior = server.mcp.settings.model_copy(deep=True)
    try:
        server._configure_public_transport(allowed)
        assert server.mcp.settings.host == "0.0.0.0"
        assert server.mcp.settings.port == 8000
        assert server.mcp.settings.stateless_http is True
        assert server.mcp.settings.transport_security.allowed_hosts == ["research.example.org"]
    finally:
        server.mcp.settings = prior


def test_remote_transport_without_public_mode_is_rejected(tmp_path: Path) -> None:
    with patch.object(server.mcp, "run"):
        with pytest.raises(ValueError, match="only in reviewed --public-demo mode"):
            server.main([
                "--root", str(tmp_path), "--transport", "streamable-http",
            ])


def test_public_server_verifies_manifest_before_streamable_http(tmp_path: Path) -> None:
    root = tmp_path / "research"
    _project(root)
    write_manifest(root, build_manifest(
        root,
        corpus_id="public-fixture",
        display_name="Public Fixture",
        confirmation=PUBLIC_CONFIRMATION,
    ))
    previous_paths = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    previous_state = server.PUBLIC_DEMO_STATE
    previous_settings = server.mcp.settings.model_copy(deep=True)
    try:
        with (
            patch.object(server, "_restrict_public_tool_surface") as restrict,
            patch.object(server.mcp, "run") as run,
        ):
            assert server.main([
                "--root", str(root), "--transport", "streamable-http", "--public-demo",
            ]) == 0
        restrict.assert_called_once_with()
        run.assert_called_once_with(transport="streamable-http")
        assert server.PUBLIC_DEMO_STATE["status"] == "verified"
        assert server.public_demo_status()["application_version"] == "0.8.2"
    finally:
        server.configure_runtime(*previous_paths)
        server.PUBLIC_DEMO_STATE = previous_state
        server.mcp.settings = previous_settings
