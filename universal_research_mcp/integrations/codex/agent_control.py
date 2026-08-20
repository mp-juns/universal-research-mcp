"""Host-owned Codex subagent policy and descendant-turn control.

The MCP may inspect or prepare an immutable proposal.  Only the separate host
CLI applies a proposal, writes the dedicated Codex profile, or interrupts a
turn.  Targets are restricted to descendants of one explicit root thread; the
root thread and unrelated user threads are never eligible.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect, unix_connect


_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_ACTIONS = frozenset({"disable", "enable", "stop_active"})
_SUBAGENT_SOURCE_KINDS = [
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
]


class CodexAgentControlError(RuntimeError):
    """Raised when a host-agent operation cannot be proven safe."""


class RpcClient(Protocol):
    def rpc(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise CodexAgentControlError(f"{label} is invalid")
    return value


def _control_root(root: str | Path) -> Path:
    return Path(root).resolve() / "data/host-control/codex-agents"


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexAgentControlError(f"{label} cannot be read") from exc
    if not isinstance(value, dict):
        raise CodexAgentControlError(f"{label} must be an object")
    return value


def _proposal_body(proposal: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in proposal.items() if key != "proposal_hash"}


def _validate_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "action", "ancestor_thread_id", "profile_name",
        "agents_enabled", "multi_agent_feature_enabled", "stop_active_descendants",
        "created_at",
    }
    if set(proposal) != required | {"proposal_hash"}:
        raise CodexAgentControlError("agent-control proposal fields are invalid")
    if proposal.get("schema_version") != "codex-agent-control-proposal/1.0":
        raise CodexAgentControlError("agent-control proposal schema is unsupported")
    action = proposal.get("action")
    if action not in _ACTIONS:
        raise CodexAgentControlError("agent-control action is unsupported")
    ancestor = proposal.get("ancestor_thread_id")
    profile = proposal.get("profile_name")
    if not isinstance(ancestor, str):
        raise CodexAgentControlError("ancestor_thread_id is invalid")
    if not isinstance(profile, str):
        raise CodexAgentControlError("profile_name is invalid")
    _identifier(ancestor, "ancestor_thread_id")
    _identifier(profile, "profile_name")
    expected = {
        "disable": (False, False, True),
        "enable": (True, True, False),
        "stop_active": (None, None, True),
    }[str(action)]
    actual = (
        proposal.get("agents_enabled"),
        proposal.get("multi_agent_feature_enabled"),
        proposal.get("stop_active_descendants"),
    )
    if actual != expected:
        raise CodexAgentControlError("agent-control action fields do not match the fixed policy")
    if proposal.get("proposal_hash") != _hash(_proposal_body(proposal)):
        raise CodexAgentControlError("agent-control proposal hash mismatch")
    return dict(proposal)


class CodexAppServerClient:
    """Minimal JSON-RPC client for a local Codex WebSocket transport."""

    def __init__(
        self,
        *,
        socket_path: str | Path | None = None,
        url: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if socket_path is not None and url is not None:
            raise CodexAgentControlError("configure either a Codex socket or URL, not both")
        if url is not None:
            parsed = urlparse(url)
            if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
                raise CodexAgentControlError("Codex app-server URL must be local loopback WebSocket")
        self.socket_path = None if socket_path is None else Path(socket_path).resolve()
        self.url = url
        self.timeout_seconds = timeout_seconds

    def rpc(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if self.socket_path is None and self.url is None:
            raise CodexAgentControlError("Codex app-server endpoint is not configured")
        initialize = {
            "method": "initialize",
            "id": 0,
            "params": {
                "clientInfo": {
                    "name": "universal_research_mcp",
                    "title": "Universal Research MCP",
                    "version": "1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        }
        deadline = time.monotonic() + self.timeout_seconds

        def receive(connection: Any, response_id: int) -> dict[str, Any]:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexAgentControlError("Codex app-server response timed out")
                try:
                    raw = connection.recv(timeout=remaining)
                except TimeoutError as exc:
                    raise CodexAgentControlError("Codex app-server response timed out") from exc
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                if not isinstance(raw, str):
                    raise CodexAgentControlError("Codex app-server response type is invalid")
                try:
                    response = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise CodexAgentControlError("Codex app-server emitted malformed JSON") from exc
                if not isinstance(response, dict) or response.get("id") != response_id:
                    continue
                if "error" in response:
                    raise CodexAgentControlError("Codex app-server returned an error")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise CodexAgentControlError("Codex app-server response is malformed")
                return result

        try:
            if self.url is not None:
                transport = connect(
                    self.url,
                    open_timeout=self.timeout_seconds,
                    close_timeout=1,
                    proxy=None,
                )
            else:
                transport = unix_connect(
                    str(self.socket_path),
                    uri="ws://localhost/",
                    open_timeout=self.timeout_seconds,
                    close_timeout=1,
                )
            with transport as connection:
                connection.send(json.dumps(initialize, separators=(",", ":")))
                receive(connection, 0)
                connection.send(json.dumps({"method": "initialized", "params": {}}, separators=(",", ":")))
                request = {"method": method, "id": 1, "params": dict(params)}
                connection.send(json.dumps(request, separators=(",", ":")))
                return receive(connection, 1)
        except (OSError, WebSocketException) as exc:
            raise CodexAgentControlError("Codex app-server Unix WebSocket is unavailable") from exc


def _client_from_environment() -> CodexAppServerClient:
    url = os.environ.get("UNIVERSAL_RESEARCH_CODEX_APP_SERVER_URL")
    if url:
        return CodexAppServerClient(url=url)
    raw = os.environ.get("UNIVERSAL_RESEARCH_CODEX_APP_SERVER_SOCKET")
    if raw:
        socket_path = Path(raw)
    else:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        socket_path = codex_home / "app-server-control/app-server-control.sock"
    return CodexAppServerClient(socket_path=socket_path)


def _descendants(client: RpcClient, ancestor_thread_id: str) -> list[dict[str, Any]]:
    cursor: str | None = None
    descendants: list[dict[str, Any]] = []
    while True:
        result = client.rpc("thread/list", {
            "ancestorThreadId": ancestor_thread_id,
            "archived": False,
            "sourceKinds": _SUBAGENT_SOURCE_KINDS,
            "limit": 100,
            "cursor": cursor,
        })
        rows = result.get("data")
        if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
            raise CodexAgentControlError("Codex descendant list is malformed")
        for row in rows:
            thread_id = row.get("id")
            if not isinstance(thread_id, str) or thread_id == ancestor_thread_id:
                raise CodexAgentControlError("Codex returned an invalid descendant")
            if row.get("parentThreadId") is None:
                raise CodexAgentControlError("Codex returned a non-subagent thread")
            descendants.append(row)
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise CodexAgentControlError("Codex descendant pagination is invalid")
        cursor = next_cursor
    return descendants


def codex_agent_status(
    *,
    ancestor_thread_id: str | None = None,
    client: RpcClient | None = None,
) -> dict[str, Any]:
    """Return metadata-only status for subagents descended from one root thread."""

    root_id = _identifier(
        ancestor_thread_id or os.environ.get("CODEX_THREAD_ID", ""),
        "ancestor_thread_id",
    )
    rows = _descendants(client or _client_from_environment(), root_id)
    active_agents = []
    for row in rows:
        status = row.get("status") if isinstance(row.get("status"), dict) else {}
        if status.get("type") == "active":
            active_agents.append({
                "thread_id": row["id"],
                "parent_thread_id": row.get("parentThreadId"),
                "status": "active",
                "active_flags": list(status.get("activeFlags") or []),
            })
    return {
        "schema_version": "codex-agent-control-status/1.0",
        "ancestor_thread_id": root_id,
        "descendant_count": len(rows),
        "active_count": len(active_agents),
        "inactive_count": len(rows) - len(active_agents),
        "agents": active_agents,
        "agent_details_scope": "active_descendants_only",
        "thread_content_included": False,
        "root_thread_targetable": False,
    }


def prepare_codex_agent_control(
    root: str | Path,
    *,
    action: str,
    ancestor_thread_id: str | None = None,
    profile_name: str = "universal-research-governed",
) -> dict[str, Any]:
    """Create an immutable proposal without changing Codex or interrupting work."""

    if action not in _ACTIONS:
        raise CodexAgentControlError("agent-control action is unsupported")
    root_id = _identifier(
        ancestor_thread_id or os.environ.get("CODEX_THREAD_ID", ""),
        "ancestor_thread_id",
    )
    profile = _identifier(profile_name, "profile_name")
    enabled, feature_enabled, stop = {
        "disable": (False, False, True),
        "enable": (True, True, False),
        "stop_active": (None, None, True),
    }[action]
    body: dict[str, Any] = {
        "schema_version": "codex-agent-control-proposal/1.0",
        "action": action,
        "ancestor_thread_id": root_id,
        "profile_name": profile,
        "agents_enabled": enabled,
        "multi_agent_feature_enabled": feature_enabled,
        "stop_active_descendants": stop,
        "created_at": _now(),
    }
    proposal = {**body, "proposal_hash": _hash(body)}
    path = _control_root(root) / "pending" / f"{proposal['proposal_hash']}.json"
    if path.exists():
        if _read_json(path, "agent-control proposal") != proposal:
            raise CodexAgentControlError("existing agent-control proposal is inconsistent")
    else:
        _write_new_json(path, proposal)
    return {
        "status": "prepared",
        "proposal_hash": proposal["proposal_hash"],
        "action": action,
        "ancestor_thread_id": root_id,
        "canonical_changed": False,
        "host_changed": False,
        "requires_host_cli_confirmation": True,
    }


def _profile_path(codex_home: str | Path, profile_name: str) -> Path:
    raw_home = Path(codex_home)
    if raw_home.is_symlink():
        raise CodexAgentControlError("CODEX_HOME must not be a symlink")
    home = raw_home.resolve()
    candidate = home / f"{profile_name}.config.toml"
    if candidate.is_symlink() or candidate.parent.resolve() != home:
        raise CodexAgentControlError("Codex profile path is unsafe")
    return candidate


def _write_profile(path: Path, *, enabled: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise CodexAgentControlError("Codex profile must not be a symlink")
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CodexAgentControlError("Codex profile cannot be read") from exc
        if not existing.startswith("# Managed by Universal Research host control.\n"):
            raise CodexAgentControlError("refusing to replace an unmanaged Codex profile")
    rendered = (
        "# Managed by Universal Research host control.\n"
        "[agents]\n"
        f"enabled = {'true' if enabled else 'false'}\n\n"
        "[features]\n"
        f"multi_agent = {'true' if enabled else 'false'}\n"
    )
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise CodexAgentControlError("Codex profile temporary path is unsafe")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _set_global_multi_agent_feature(
    enabled: bool,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Use Codex's own config writer instead of rewriting user config.toml."""

    action = "enable" if enabled else "disable"
    try:
        completed = runner(
            ["codex", "features", action, "multi_agent"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexAgentControlError("Codex feature configuration is unavailable") from exc
    if completed.returncode != 0:
        raise CodexAgentControlError("Codex rejected the multi-agent feature change")


def _interrupt_descendants(client: RpcClient, ancestor_thread_id: str) -> dict[str, Any]:
    before = codex_agent_status(ancestor_thread_id=ancestor_thread_id, client=client)
    interrupted: list[dict[str, str]] = []
    for agent in before["agents"]:
        if agent["status"] != "active":
            continue
        thread_id = agent["thread_id"]
        result = client.rpc("thread/read", {"threadId": thread_id, "includeTurns": True})
        thread = result.get("thread")
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise CodexAgentControlError("Codex thread read is malformed")
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise CodexAgentControlError("Codex active turn list is malformed")
        active_turns = [item for item in turns if isinstance(item, dict) and item.get("status") == "inProgress"]
        if len(active_turns) > 1:
            raise CodexAgentControlError("Codex reported multiple active turns for one subagent")
        if not active_turns:
            raise CodexAgentControlError("Codex active subagent has no interruptible turn")
        turn_id = active_turns[0].get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise CodexAgentControlError("Codex active turn id is invalid")
        client.rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        interrupted.append({"thread_id": thread_id, "turn_id": turn_id})
    if len(interrupted) != before["active_count"]:
        raise CodexAgentControlError("not every active Codex subagent was interrupted")
    return {
        "targeted_descendant_count": before["descendant_count"],
        "active_before": before["active_count"],
        "interrupted_count": len(interrupted),
        "interrupted": interrupted,
        "root_thread_interrupted": False,
    }


def _apply_codex_agent_control_unlocked(
    root: str | Path,
    *,
    proposal_hash: str,
    confirm_proposal_hash: str,
    codex_home: str | Path | None = None,
    client: RpcClient | None = None,
    feature_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Apply one exact host-confirmed proposal and consume it once."""

    if proposal_hash != confirm_proposal_hash:
        raise CodexAgentControlError("confirmation does not match proposal_hash")
    if re.fullmatch(r"[0-9a-f]{64}", proposal_hash) is None:
        raise CodexAgentControlError("proposal_hash is invalid")
    control = _control_root(root)
    proposal = _validate_proposal(_read_json(
        control / "pending" / f"{proposal_hash}.json",
        "agent-control proposal",
    ))
    consumed_path = control / "consumed" / f"{proposal_hash}.json"
    if consumed_path.exists():
        raise CodexAgentControlError("agent-control proposal was already consumed")
    profile_path: Path | None = None
    if proposal["agents_enabled"] is not None:
        selected_home = codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        profile_path = _profile_path(selected_home, proposal["profile_name"])
        _set_global_multi_agent_feature(
            bool(proposal["multi_agent_feature_enabled"]),
            runner=feature_runner,
        )
        _write_profile(profile_path, enabled=bool(proposal["agents_enabled"]))
    stop_result = {
        "targeted_descendant_count": 0,
        "active_before": 0,
        "interrupted_count": 0,
        "interrupted": [],
        "root_thread_interrupted": False,
    }
    if proposal["stop_active_descendants"] is True:
        stop_result = _interrupt_descendants(client or _client_from_environment(), proposal["ancestor_thread_id"])
    consumed = {
        "schema_version": "codex-agent-control-consumption/1.0",
        "proposal_hash": proposal_hash,
        "action": proposal["action"],
        "ancestor_thread_id": proposal["ancestor_thread_id"],
        "consumed_at": _now(),
        "profile_path": None if profile_path is None else str(profile_path),
        "profile_restart_required": profile_path is not None,
        "global_multi_agent_feature_changed": profile_path is not None,
        "stop_result": stop_result,
    }
    _write_new_json(consumed_path, consumed)
    return {"status": "applied", **consumed}


def apply_codex_agent_control(
    root: str | Path,
    *,
    proposal_hash: str,
    confirm_proposal_hash: str,
    codex_home: str | Path | None = None,
    client: RpcClient | None = None,
    feature_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Apply one exact proposal under a per-proposal exclusive host lock."""

    if proposal_hash != confirm_proposal_hash:
        raise CodexAgentControlError("confirmation does not match proposal_hash")
    if re.fullmatch(r"[0-9a-f]{64}", proposal_hash) is None:
        raise CodexAgentControlError("proposal_hash is invalid")
    lock_path = _control_root(root) / "inflight" / f"{proposal_hash}.json"
    try:
        _write_new_json(lock_path, {
            "schema_version": "codex-agent-control-lock/1.0",
            "proposal_hash": proposal_hash,
            "created_at": _now(),
            "pid": os.getpid(),
        })
    except FileExistsError as exc:
        raise CodexAgentControlError("agent-control proposal is already in progress") from exc
    try:
        return _apply_codex_agent_control_unlocked(
            root,
            proposal_hash=proposal_hash,
            confirm_proposal_hash=confirm_proposal_hash,
            codex_home=codex_home,
            client=client,
            feature_runner=feature_runner,
        )
    finally:
        lock_path.unlink(missing_ok=True)


__all__ = [
    "CodexAgentControlError",
    "CodexAppServerClient",
    "apply_codex_agent_control",
    "codex_agent_status",
    "prepare_codex_agent_control",
]
