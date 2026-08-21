"""Host Codex adapter with no general-purpose tool surface."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Mapping

from universal_research_mcp.governance.hashing import artifact_hash

from .claims import output_schema
from .contracts import HarnessContractError, validate_run_plan


FORBIDDEN_EVENT_ITEM_TYPES = frozenset({
    "command_execution", "file_change", "web_search", "computer_use",
})
WORKER_TOOLS = ["worker_read", "worker_search", "worker_write", "worker_execute", "worker_inventory"]
_SAFE_ENV_NAMES = frozenset({
    "PATH", "HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_STATE_HOME", "XDG_CACHE_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "LANG", "LC_ALL", "TERM",
})


def codex_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep local Codex configuration while excluding provider credentials."""
    source = os.environ if environ is None else environ
    return {name: value for name, value in source.items() if name in _SAFE_ENV_NAMES}


@dataclass(frozen=True)
class CodexResult:
    final_output: dict[str, Any]
    usage: dict[str, int]
    model: str
    events_hash: str


def codex_command(
    plan: Mapping[str, Any],
    *,
    control_root: str | Path,
    project_root: str | Path,
    plan_path: str | Path,
    manifest_path: str | Path,
    workspace_path: str | Path,
    schema_path: str | Path,
    approval_state_root: str | Path | None = None,
) -> list[str]:
    normalized = validate_run_plan(plan)
    selected_approval_state = (
        Path(approval_state_root)
        if approval_state_root is not None
        else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    ).expanduser().resolve()
    args = [
        sys.executable, "-m", "universal_research_mcp.secure_harness.worker_server",
        "--root", str(Path(project_root).resolve()), "--plan", str(Path(plan_path).resolve()),
        "--manifest", str(Path(manifest_path).resolve()), "--workspace", str(Path(workspace_path).resolve()),
        "--approval-state-root", str(selected_approval_state),
    ]
    return [
        "codex", "exec", "--json", "--ephemeral", "--ignore-user-config",
        "--strict-config",
        "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", normalized["model"], "--cd", str(Path(control_root).resolve()),
        "--output-schema", str(Path(schema_path).resolve()),
        "-c", f'model_reasoning_effort="{normalized["reasoning_effort"]}"',
        "-c", "features.shell_tool=false",
        "-c", "agents.enabled=false",
        "-c", "features.multi_agent=false",
        "-c", "features.multi_agent_v2=false",
        "-c", "features.apps=false",
        "-c", "features.browser_use=false",
        "-c", "features.computer_use=false",
        "-c", "features.image_generation=false",
        "-c", "tools.web_search=false",
        "-c", "tools.view_image=false",
        "-c", 'web_search="disabled"',
        "-c", 'mcp_servers.ur_worker.command="' + sys.executable.replace('"', '') + '"',
        "-c", "mcp_servers.ur_worker.args=" + json.dumps(args[1:]),
        "-c", "mcp_servers.ur_worker.required=true",
        "-c", "mcp_servers.ur_worker.enabled_tools=" + json.dumps(WORKER_TOOLS),
        "-",
    ]


class CodexRunner:
    def __init__(self, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self._runner = runner or subprocess.run

    def run(
        self,
        plan: Mapping[str, Any],
        *,
        prompt: str,
        control_root: str | Path,
        project_root: str | Path,
        plan_path: str | Path,
        manifest_path: str | Path,
        workspace_path: str | Path,
        schema_path: str | Path,
        approval_state_root: str | Path | None = None,
    ) -> CodexResult:
        normalized = validate_run_plan(plan)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 1024 * 1024:
            raise HarnessContractError("Codex prompt is empty or too large")
        command = codex_command(
            normalized,
            control_root=control_root,
            project_root=project_root,
            plan_path=plan_path,
            manifest_path=manifest_path,
            workspace_path=workspace_path,
            schema_path=schema_path,
            approval_state_root=approval_state_root,
        )
        completed = self._runner(
            command,
            input=prompt,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(item["timeout_seconds"] for item in normalized["operations"]) + 120,
            check=False,
            env=codex_environment(),
        )
        if completed.returncode != 0:
            raise RuntimeError("Codex worker failed without an eligible result")
        events: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None
        final_text: str | None = None
        for raw in completed.stdout.splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise HarnessContractError("Codex emitted non-JSONL output") from exc
            if not isinstance(event, dict):
                raise HarnessContractError("Codex event must be an object")
            events.append(event)
            item = event.get("item")
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type in FORBIDDEN_EVENT_ITEM_TYPES or item_type == "agent_message" and isinstance(item.get("text"), str):
                    if item_type in FORBIDDEN_EVENT_ITEM_TYPES:
                        raise HarnessContractError(f"Codex used forbidden tool surface: {item_type}")
                    final_text = item["text"]
            if event.get("type") == "turn.completed":
                raw_usage = event.get("usage")
                if not isinstance(raw_usage, dict):
                    raise HarnessContractError("Codex completion omitted usage")
                usage = {
                    "input_tokens": int(raw_usage.get("input_tokens", 0)),
                    "cached_input_tokens": int(raw_usage.get("cached_input_tokens", 0)),
                    "output_tokens": int(raw_usage.get("output_tokens", 0)),
                    "reasoning_output_tokens": int(raw_usage.get("reasoning_output_tokens", 0)),
                }
        if usage is None or final_text is None:
            raise HarnessContractError("Codex did not emit a complete structured result")
        total = usage["input_tokens"] + usage["output_tokens"]
        if total > normalized["resources"]["max_total_tokens"]:
            raise HarnessContractError("Codex reported token usage above the approved ceiling")
        try:
            final = json.loads(final_text)
        except json.JSONDecodeError as exc:
            raise HarnessContractError("Codex final message is not structured JSON") from exc
        if not isinstance(final, dict) or not isinstance(final.get("segments"), list):
            raise HarnessContractError("Codex final output does not match the claim envelope")
        return CodexResult(
            final_output=final,
            usage={**usage, "total_tokens": total},
            model=normalized["model"],
            events_hash=artifact_hash(events),
        )


def write_output_schema(path: str | Path) -> Path:
    candidate = Path(path)
    candidate.write_text(json.dumps(output_schema(), indent=2) + "\n", encoding="utf-8")
    return candidate
