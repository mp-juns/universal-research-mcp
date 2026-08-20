from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

import pytest

from universal_research_mcp.integrations.codex import agent_control
from universal_research_mcp.cli import build_parser
from universal_research_mcp import server
from universal_research_mcp.integrations.codex.agent_control import (
    CodexAgentControlError,
    CodexAppServerClient,
    apply_codex_agent_control,
    codex_agent_status,
    prepare_codex_agent_control,
)


class FakeCodexAppServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.interrupted: set[str] = set()

    def rpc(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        materialized = dict(params)
        self.calls.append((method, materialized))
        if method == "thread/list":
            assert materialized["ancestorThreadId"] == "root-thread"
            return {
                "data": [
                    {
                        "id": "child-active",
                        "parentThreadId": "root-thread",
                        "status": {"type": "active", "activeFlags": []},
                    },
                    {
                        "id": "child-idle",
                        "parentThreadId": "root-thread",
                        "status": {"type": "idle"},
                    },
                ],
                "nextCursor": None,
            }
        if method == "thread/read":
            assert materialized["threadId"] == "child-active"
            return {
                "thread": {
                    "id": "child-active",
                    "turns": [{"id": "turn-active", "status": "inProgress"}],
                },
            }
        if method == "turn/interrupt":
            assert materialized == {"threadId": "child-active", "turnId": "turn-active"}
            self.interrupted.add("child-active")
            return {}
        raise AssertionError(f"unexpected RPC method {method}")


def _successful_feature_change(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout="", stderr="")


def test_status_is_descendant_only_and_metadata_only() -> None:
    client = FakeCodexAppServer()
    status = codex_agent_status(ancestor_thread_id="root-thread", client=client)

    assert status["descendant_count"] == 2
    assert status["active_count"] == 1
    assert status["inactive_count"] == 1
    assert status["root_thread_targetable"] is False
    assert status["thread_content_included"] is False
    assert {item["thread_id"] for item in status["agents"]} == {"child-active"}
    assert status["agent_details_scope"] == "active_descendants_only"
    assert all("preview" not in item for item in status["agents"])


def test_prepare_creates_a_new_non_idempotent_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamps = iter(("2026-08-21T00:00:00+00:00", "2026-08-21T00:00:01+00:00"))
    monkeypatch.setattr(agent_control, "_now", lambda: next(timestamps))

    first = prepare_codex_agent_control(
        tmp_path,
        action="stop_active",
        ancestor_thread_id="root-thread",
    )
    second = prepare_codex_agent_control(
        tmp_path,
        action="stop_active",
        ancestor_thread_id="root-thread",
    )

    assert first["proposal_hash"] != second["proposal_hash"]


def test_app_server_client_initializes_before_request(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.responses = iter([
                json.dumps({"id": 0, "result": {"userAgent": "fixture"}}),
                json.dumps({"id": 1, "result": {"data": [], "nextCursor": None}}),
            ])

        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def send(self, message: str) -> None:
            self.sent.append(message)

        def recv(self, timeout: float | None = None) -> str:
            assert timeout is not None and timeout > 0
            return next(self.responses)

    captured: dict[str, Any] = {}
    connection = FakeConnection()

    def fake_unix_connect(path: str, **kwargs: Any) -> FakeConnection:
        captured["path"] = path
        captured["kwargs"] = kwargs
        return connection

    monkeypatch.setattr(agent_control, "unix_connect", fake_unix_connect)
    result = CodexAppServerClient(socket_path="/tmp/codex.sock").rpc(
        "thread/list", {"ancestorThreadId": "root-thread"},
    )
    assert result == {"data": [], "nextCursor": None}
    assert captured["path"] == "/tmp/codex.sock"
    assert captured["kwargs"]["uri"] == "ws://localhost/"
    messages = [json.loads(message) for message in connection.sent]
    assert messages[0]["method"] == "initialize"
    assert messages[0]["params"]["capabilities"]["experimentalApi"] is True
    assert messages[1] == {"method": "initialized", "params": {}}
    assert messages[2]["method"] == "thread/list"


def test_app_server_client_accepts_only_loopback_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CodexAgentControlError, match="local loopback"):
        CodexAppServerClient(url="wss://remote.example/app-server")

    connection = type("Connection", (), {
        "__enter__": lambda self: self,
        "__exit__": lambda self, *_args: None,
        "send": lambda self, _message: None,
        "recv": lambda self, timeout=None: next(self.responses),
        "responses": iter([
            json.dumps({"id": 0, "result": {}}),
            json.dumps({"id": 1, "result": {"data": [], "nextCursor": None}}),
        ]),
    })()
    captured: dict[str, Any] = {}

    def fake_connect(url: str, **kwargs: Any) -> Any:
        captured.update(url=url, kwargs=kwargs)
        return connection

    monkeypatch.setattr(agent_control, "connect", fake_connect)
    result = CodexAppServerClient(url="ws://127.0.0.1:45678").rpc("thread/list", {})
    assert result == {"data": [], "nextCursor": None}
    assert captured["url"] == "ws://127.0.0.1:45678"
    assert captured["kwargs"]["proxy"] is None


def test_disable_is_nonexecuting_until_host_apply_then_stops_active_descendants(tmp_path: Path) -> None:
    root = tmp_path / "research"
    codex_home = tmp_path / "codex-home"
    client = FakeCodexAppServer()

    prepared = prepare_codex_agent_control(
        root,
        action="disable",
        ancestor_thread_id="root-thread",
    )
    profile = codex_home / "universal-research-governed.config.toml"
    assert prepared["host_changed"] is False
    assert not profile.exists()
    assert client.calls == []

    applied = apply_codex_agent_control(
        root,
        proposal_hash=prepared["proposal_hash"],
        confirm_proposal_hash=prepared["proposal_hash"],
        codex_home=codex_home,
        client=client,
        feature_runner=_successful_feature_change,
    )
    rendered = profile.read_text(encoding="utf-8")
    assert "enabled = false" in rendered
    assert "multi_agent = false" in rendered
    assert applied["stop_result"]["interrupted_count"] == 1
    assert applied["stop_result"]["root_thread_interrupted"] is False
    assert client.interrupted == {"child-active"}

    with pytest.raises(CodexAgentControlError, match="already consumed"):
        apply_codex_agent_control(
            root,
            proposal_hash=prepared["proposal_hash"],
            confirm_proposal_hash=prepared["proposal_hash"],
            codex_home=codex_home,
            client=client,
            feature_runner=_successful_feature_change,
        )


def test_enable_requires_exact_confirmation_and_does_not_interrupt(tmp_path: Path) -> None:
    root = tmp_path / "research"
    codex_home = tmp_path / "codex-home"
    client = FakeCodexAppServer()
    prepared = prepare_codex_agent_control(
        root,
        action="enable",
        ancestor_thread_id="root-thread",
    )
    with pytest.raises(CodexAgentControlError, match="confirmation"):
        apply_codex_agent_control(
            root,
            proposal_hash=prepared["proposal_hash"],
            confirm_proposal_hash="0" * 64,
            codex_home=codex_home,
            client=client,
            feature_runner=_successful_feature_change,
        )
    applied = apply_codex_agent_control(
        root,
        proposal_hash=prepared["proposal_hash"],
        confirm_proposal_hash=prepared["proposal_hash"],
        codex_home=codex_home,
        client=client,
        feature_runner=_successful_feature_change,
    )
    rendered = Path(applied["profile_path"]).read_text(encoding="utf-8")
    assert "enabled = true" in rendered
    assert "multi_agent = true" in rendered
    assert client.calls == []


def test_host_policy_uses_default_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "research"
    home = tmp_path / "home"
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))
    prepared = prepare_codex_agent_control(
        root,
        action="enable",
        ancestor_thread_id="root-thread",
    )

    applied = apply_codex_agent_control(
        root,
        proposal_hash=prepared["proposal_hash"],
        confirm_proposal_hash=prepared["proposal_hash"],
        client=FakeCodexAppServer(),
        feature_runner=_successful_feature_change,
    )

    assert Path(applied["profile_path"]) == (
        home / ".codex/universal-research-governed.config.toml"
    )


def test_apply_rejects_a_concurrent_proposal_lock(tmp_path: Path) -> None:
    root = tmp_path / "research"
    prepared = prepare_codex_agent_control(
        root,
        action="stop_active",
        ancestor_thread_id="root-thread",
    )
    lock = root / "data/host-control/codex-agents/inflight" / f"{prepared['proposal_hash']}.json"
    lock.parent.mkdir(parents=True)
    lock.write_text("{}\n", encoding="utf-8")

    with pytest.raises(CodexAgentControlError, match="already in progress"):
        apply_codex_agent_control(
            root,
            proposal_hash=prepared["proposal_hash"],
            confirm_proposal_hash=prepared["proposal_hash"],
            client=FakeCodexAppServer(),
        )


def test_tampered_proposal_and_non_subagent_rows_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "research"
    prepared = prepare_codex_agent_control(
        root,
        action="stop_active",
        ancestor_thread_id="root-thread",
    )
    path = root / "data/host-control/codex-agents/pending" / f"{prepared['proposal_hash']}.json"
    proposal = json.loads(path.read_text(encoding="utf-8"))
    proposal["ancestor_thread_id"] = "different-root"
    path.write_text(json.dumps(proposal), encoding="utf-8")
    with pytest.raises(CodexAgentControlError, match="hash mismatch"):
        apply_codex_agent_control(
            root,
            proposal_hash=prepared["proposal_hash"],
            confirm_proposal_hash=prepared["proposal_hash"],
            client=FakeCodexAppServer(),
        )

    class InvalidServer:
        def rpc(self, _method: str, _params: Mapping[str, Any]) -> dict[str, Any]:
            return {"data": [{"id": "unrelated", "parentThreadId": None, "status": {"type": "active"}}], "nextCursor": None}

    with pytest.raises(CodexAgentControlError, match="non-subagent"):
        codex_agent_status(ancestor_thread_id="root-thread", client=InvalidServer())


def test_cli_surface_keeps_apply_separate_from_mcp() -> None:
    parser = build_parser()
    prepared = parser.parse_args(["codex-agents", "prepare", "disable"])
    assert prepared.command == "codex-agents"
    assert prepared.codex_agents_action == "prepare"
    with pytest.raises(SystemExit):
        parser.parse_args([
            "codex-agents", "prepare", "disable",
            "--ancestor-thread-id", "unrelated-root",
        ])
    applied = parser.parse_args([
        "codex-agents", "apply", "a" * 64, "--confirm-proposal-hash", "a" * 64,
    ])
    assert applied.codex_agents_action == "apply"


def test_mcp_exposes_status_and_prepare_but_not_host_apply() -> None:
    tools = {item.name: item for item in asyncio.run(server.mcp.list_tools())}
    assert "codex_host_agent_status" in tools
    assert "codex_prepare_agent_control" in tools
    assert "codex_apply_agent_control" not in tools
    assert "ancestor_thread_id" not in tools["codex_host_agent_status"].inputSchema.get("properties", {})
    assert "ancestor_thread_id" not in tools["codex_prepare_agent_control"].inputSchema.get("properties", {})
    assert tools["codex_prepare_agent_control"].annotations.idempotentHint is False
