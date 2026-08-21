from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from universal_research_mcp import server
from universal_research_mcp.cli import build_parser, legacy_main, main
from universal_research_mcp.integrations.codex.agent_control import (
    CodexAppServerClient,
    CodexAgentControlError,
    apply_codex_agent_control,
    codex_agent_status,
    prepare_codex_agent_control,
)


def test_removed_app_server_client_name_fails_closed_compatibly() -> None:
    with pytest.raises(CodexAgentControlError, match="protected host broker required"):
        CodexAppServerClient("ws://127.0.0.1:9999")


def test_status_fails_closed_without_reading_spoofable_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_THREAD_ID", "forged-unrelated-thread")
    monkeypatch.setenv("UNIVERSAL_RESEARCH_CODEX_APP_SERVER_URL", "ws://127.0.0.1:9")

    status = codex_agent_status()

    assert status["status"] == "unavailable"
    assert status["reason_code"] == "protected_host_broker_required"
    assert status["authoritative_thread_identity_available"] is False
    assert status["protected_host_approval_receipt_available"] is False
    assert status["current_session_capability_revocation_available"] is False
    assert status["proposal_created"] is False
    assert status["host_changed"] is False
    assert "ancestor_thread_id" not in status
    assert "forged-unrelated-thread" not in json.dumps(status)


@pytest.mark.parametrize("action", ["disable", "enable", "stop_active"])
def test_prepare_is_non_mutating_and_returns_unavailable(
    tmp_path: Path,
    action: str,
) -> None:
    root = tmp_path / "research"

    result = prepare_codex_agent_control(root, action=action)

    assert result["status"] == "unavailable"
    assert result["requested_action"] == action
    assert result["proposal_created"] is False
    assert result["canonical_changed"] is False
    assert result["host_changed"] is False
    assert not root.exists()


def test_prepare_rejects_unknown_action_without_writing(tmp_path: Path) -> None:
    with pytest.raises(CodexAgentControlError, match="unsupported"):
        prepare_codex_agent_control(tmp_path, action="delete_everything")
    assert list(tmp_path.iterdir()) == []


def test_direct_python_apply_is_rejected_before_any_side_effect(tmp_path: Path) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    class Client:
        def rpc(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("App Server must not be contacted")

    with pytest.raises(CodexAgentControlError, match="protected host broker required"):
        apply_codex_agent_control(
            tmp_path / "research",
            proposal_hash="a" * 64,
            confirm_proposal_hash="a" * 64,
            codex_home=tmp_path / "codex-home",
            client=Client(),
            feature_runner=runner,
        )

    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_public_cli_has_status_but_no_prepare_or_apply() -> None:
    parser = build_parser()
    status = parser.parse_args(["codex-agents", "status"])
    assert status.command == "codex-agents"
    assert status.codex_agents_action == "status"

    with pytest.raises(SystemExit):
        parser.parse_args(["codex-agents", "prepare", "disable"])
    with pytest.raises(SystemExit):
        parser.parse_args([
            "codex-agents", "apply", "a" * 64,
            "--confirm-proposal-hash", "a" * 64,
        ])


def test_cli_status_is_machine_readable_and_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["codex-agents", "status", "--root", str(tmp_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "protected_host_broker_required"


def test_legacy_cli_status_is_machine_readable_and_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert legacy_main(["codex-agents", "status", "--root", str(tmp_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unavailable"
    assert result["proposal_created"] is False


def test_mcp_tools_are_read_only_and_fail_closed(tmp_path: Path) -> None:
    tools = {item.name: item for item in asyncio.run(server.mcp.list_tools())}
    assert "codex_host_agent_status" in tools
    assert "codex_prepare_agent_control" in tools
    assert "codex_apply_agent_control" not in tools
    assert "ancestor_thread_id" not in tools["codex_host_agent_status"].inputSchema.get(
        "properties", {},
    )
    assert "ancestor_thread_id" not in tools["codex_prepare_agent_control"].inputSchema.get(
        "properties", {},
    )
    assert tools["codex_host_agent_status"].annotations.readOnlyHint is True
    assert tools["codex_prepare_agent_control"].annotations.readOnlyHint is True
    assert tools["codex_prepare_agent_control"].annotations.idempotentHint is True
    assert tools["codex_prepare_agent_control"].annotations.destructiveHint is False
    assert tools["codex_prepare_agent_control"].annotations.openWorldHint is False

    status = server.codex_host_agent_status()
    prepared = server.codex_prepare_agent_control("disable")
    assert status["status"] == "unavailable"
    assert prepared["status"] == "unavailable"
    assert prepared["proposal_created"] is False
    assert not tmp_path.joinpath("data").exists()
