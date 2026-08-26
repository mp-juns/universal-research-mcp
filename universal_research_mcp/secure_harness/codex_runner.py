"""Host Codex adapter with no general-purpose tool surface."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
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
WORKER_TOOL_RECEIPT_VERSION = "codex-worker-tool-receipt/1.0"
_SAFE_EVENT_ITEM_TYPES = ("agent_message", "mcp_tool_call", "reasoning")
_SAFE_ENV_NAMES = frozenset({
    "PATH", "HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_STATE_HOME", "XDG_CACHE_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "LANG", "LC_ALL", "TERM",
})
_DIAGNOSTIC_LIMIT_BYTES = 4096
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)"
    r"(\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KNOWN_SECRET_VALUE = re.compile(r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_-]{8,}\b")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_RAW_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOOL_ARGUMENT_KEYS = {
    "worker_read": {"operation_id", "path", "start_line", "end_line"},
    "worker_search": {"operation_id", "query", "limit"},
    "worker_write": {"operation_id", "path", "expected_sha256", "content"},
    "worker_execute": {"operation_id"},
    "worker_inventory": set(),
}
_TOOL_REQUIRED_ARGUMENT_KEYS = {
    **_TOOL_ARGUMENT_KEYS,
    "worker_search": {"operation_id", "query"},
}
_TOOL_FOR_OPERATION_KIND = {
    "read": "worker_read",
    "search": "worker_search",
    "patch": "worker_write",
    "test": "worker_execute",
    "build": "worker_execute",
    "experiment": "worker_execute",
}
_COMMON_RECEIPT_KEYS = {
    "schema_version", "sequence", "server", "tool", "status", "operation_id",
    "arguments_hash", "result_hash", "receipt_hash",
}
_TOOL_RECEIPT_KEYS = {
    "worker_read": {
        "path", "requested_start_line", "requested_end_line", "returned_start_line",
        "returned_end_line", "source_sha256", "content_sha256",
    },
    "worker_search": {"match_count", "truncated"},
    "worker_write": {"path", "output_sha256"},
    "worker_execute": {
        "exit_code", "success", "command_hash", "stdout_sha256", "stderr_sha256",
    },
    "worker_inventory": {"file_count", "file_manifest_hash", "inventory_result_hash"},
}


def codex_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep local Codex configuration while excluding provider credentials."""
    source = os.environ if environ is None else environ
    return {name: value for name, value in source.items() if name in _SAFE_ENV_NAMES}


def worker_mcp_environment(project_root: str | Path) -> dict[str, str]:
    """Pin the stdio worker to the exact sealed source without forwarding host paths."""
    try:
        source_root = Path(project_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise HarnessContractError("worker MCP source root is unavailable") from exc
    if not source_root.is_dir():
        raise HarnessContractError("worker MCP source root must be a directory")
    return {
        "PYTHONPATH": str(source_root),
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _stream_sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _bounded_redacted_stderr(value: str) -> tuple[str, bool]:
    sanitized = _ANSI_ESCAPE.sub("", value)
    sanitized = "".join(
        character if character in "\n\r\t" or ord(character) >= 32 else "�"
        for character in sanitized
    )
    sanitized = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        sanitized,
    )
    sanitized = _BEARER_VALUE.sub("Bearer [REDACTED]", sanitized)
    sanitized = _KNOWN_SECRET_VALUE.sub("[REDACTED]", sanitized)
    encoded = sanitized.encode("utf-8", errors="replace")
    if len(encoded) <= _DIAGNOSTIC_LIMIT_BYTES:
        return sanitized.strip(), False
    marker = b"[truncated]\n"
    tail = encoded[-(_DIAGNOSTIC_LIMIT_BYTES - len(marker)):]
    return (marker.decode("ascii") + tail.decode("utf-8", errors="ignore")).strip(), True


class CodexWorkerProcessError(RuntimeError):
    """A nonzero Codex exit with bounded diagnostics and no stdout disclosure."""

    def __init__(self, completed: subprocess.CompletedProcess[str]) -> None:
        stdout = completed.stdout if isinstance(completed.stdout, str) else ""
        stderr = completed.stderr if isinstance(completed.stderr, str) else ""
        stderr_tail, stderr_truncated = _bounded_redacted_stderr(stderr)
        self.diagnostic = {
            "schema_version": "codex-worker-process-failure/1.0",
            "returncode": int(completed.returncode),
            "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
            "stdout_sha256": _stream_sha256(stdout),
            "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
            "stderr_sha256": _stream_sha256(stderr),
            "stderr_tail": stderr_tail,
            "stderr_truncated": stderr_truncated,
        }
        super().__init__(
            "Codex worker failed without an eligible result: "
            + json.dumps(self.diagnostic, ensure_ascii=True, sort_keys=True)
        )


def _safe_event_metadata(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a stream without retaining attacker-controlled names or values."""
    item_type_counts = {name: 0 for name in _SAFE_EVENT_ITEM_TYPES}
    worker_tool_call_counts = {name: 0 for name in WORKER_TOOLS}
    unknown_item_type_count = 0
    unknown_mcp_tool_call_count = 0
    for event in events:
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in item_type_counts:
            item_type_counts[item_type] += 1
        elif item_type is not None:
            unknown_item_type_count += 1
        if item_type != "mcp_tool_call":
            continue
        tool = item.get("tool", item.get("name"))
        if item.get("server") == "ur_worker" and tool in worker_tool_call_counts:
            worker_tool_call_counts[tool] += 1
        else:
            unknown_mcp_tool_call_count += 1
    return {
        "event_item_type_counts": item_type_counts,
        "worker_tool_call_counts": worker_tool_call_counts,
        "unknown_item_type_count": unknown_item_type_count,
        "unknown_mcp_tool_call_count": unknown_mcp_tool_call_count,
    }


class CodexTokenCeilingError(HarnessContractError):
    """A completed stream that exceeded its approved token ceiling."""

    def __init__(
        self,
        *,
        approved_max_total_tokens: int,
        usage: Mapping[str, int],
        events: list[dict[str, Any]],
        final_text: str,
    ) -> None:
        exact_usage = {
            "input_tokens": int(usage["input_tokens"]),
            "cached_input_tokens": int(usage["cached_input_tokens"]),
            "output_tokens": int(usage["output_tokens"]),
            "reasoning_output_tokens": int(usage["reasoning_output_tokens"]),
        }
        exact_usage["total_tokens"] = exact_usage["input_tokens"] + exact_usage["output_tokens"]
        self.diagnostic = {
            "schema_version": "codex-token-ceiling-failure/1.0",
            "classification": "token_ceiling_exceeded",
            "approved_max_total_tokens": int(approved_max_total_tokens),
            "usage": exact_usage,
            "event_count": len(events),
            **_safe_event_metadata(events),
            "events_hash": artifact_hash(events),
            "final_text_bytes": len(final_text.encode("utf-8", errors="replace")),
            "final_text_sha256": _stream_sha256(final_text),
            "raw_stdout_persisted": False,
            "raw_final_text_persisted": False,
            "eligible": False,
        }
        super().__init__(
            "Codex reported token usage above the approved ceiling: "
            + json.dumps(self.diagnostic, ensure_ascii=True, sort_keys=True)
        )


@dataclass(frozen=True)
class CodexResult:
    final_output: dict[str, Any]
    usage: dict[str, int]
    model: str
    events_hash: str
    tool_receipts: list[dict[str, Any]]


def _exact_mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise HarnessContractError(f"{label} has an unsupported shape")
    return dict(value)


def _argument_mapping(value: object, tool: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessContractError("Codex worker arguments have an unsupported shape")
    keys = set(value)
    if not _TOOL_REQUIRED_ARGUMENT_KEYS[tool].issubset(keys) or not keys.issubset(
        _TOOL_ARGUMENT_KEYS[tool]
    ):
        raise HarnessContractError("Codex worker arguments have an unsupported shape")
    return dict(value)


def _operation_for_call(
    plan: Mapping[str, Any], tool: str, arguments: Mapping[str, Any], structured: Mapping[str, Any],
) -> dict[str, Any] | None:
    if tool == "worker_inventory":
        return None
    operation_id = arguments.get("operation_id")
    if not isinstance(operation_id, str) or structured.get("operation_id") != operation_id:
        raise HarnessContractError("Codex worker receipt operation binding is invalid")
    operation = next(
        (item for item in plan["operations"] if item["operation_id"] == operation_id),
        None,
    )
    if operation is None or _TOOL_FOR_OPERATION_KIND.get(operation["kind"]) != tool:
        raise HarnessContractError("Codex worker call is outside the sealed operation plan")
    return operation


def _authorized_path(operation: Mapping[str, Any], value: object) -> str:
    if not isinstance(value, str) or not value:
        raise HarnessContractError("Codex worker receipt path is invalid")
    requested = PurePosixPath(value)
    if requested.is_absolute() or ".." in requested.parts:
        raise HarnessContractError("Codex worker receipt path escapes the sealed plan")
    if not any(
        requested == PurePosixPath(root) or PurePosixPath(root) in requested.parents
        for root in operation["paths"]
    ):
        raise HarnessContractError("Codex worker receipt path is outside the sealed plan")
    return requested.as_posix()


def _raw_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _RAW_SHA256.fullmatch(value):
        raise HarnessContractError(f"{label} is not an exact SHA-256")
    return "sha256:" + value


def _integer(value: object, label: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or minimum is not None and value < minimum:
        raise HarnessContractError(f"{label} is invalid")
    return value


def _json_shape_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return "non_json"


def _result_shape_diagnostic(result: Mapping[str, Any]) -> dict[str, Any]:
    """Describe only protocol-level shape, never MCP values or field names."""
    known = (
        "content", "structuredContent", "structured_content", "isError", "_meta",
    )
    string_keys = sorted(key for key in result if isinstance(key, str))
    known_types = {
        key: _json_shape_name(result[key])
        for key in known
        if key in result
    }
    return {
        "key_count": len(result),
        "string_key_count": len(string_keys),
        "key_set_hash": artifact_hash(string_keys),
        "unexpected_key_count": sum(key not in known for key in string_keys)
        + len(result) - len(string_keys),
        "known_presence": {key: key in result for key in known},
        "known_types": known_types,
    }


def _unsupported_result_shape(result: Mapping[str, Any]) -> HarnessContractError:
    diagnostic = json.dumps(
        _result_shape_diagnostic(result), sort_keys=True, separators=(",", ":"),
    )
    return HarnessContractError(
        f"Codex worker tool result has unsupported fields; result_shape={diagnostic}"
    )


def build_worker_tool_receipt(
    item: Mapping[str, Any], plan: Mapping[str, Any], *, sequence: int,
) -> dict[str, Any]:
    """Reduce one completed Codex MCP item to bounded, plan-bound evidence."""
    normalized = validate_run_plan(plan)
    if item.get("server") != "ur_worker":
        raise HarnessContractError("Codex used an unknown MCP server")
    tool = item.get("tool")
    if tool not in WORKER_TOOLS:
        raise HarnessContractError("Codex used an unknown worker tool")
    arguments = _argument_mapping(item.get("arguments"), tool)
    if item.get("status") != "completed" or item.get("error") is not None:
        raise HarnessContractError("Codex worker tool call did not complete successfully")
    result = item.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("content"), list):
        raise HarnessContractError("Codex worker tool result has an unsupported shape")
    allowed_result_keys = {
        "content", "structuredContent", "structured_content", "isError", "_meta",
    }
    structured_keys = [
        key for key in ("structuredContent", "structured_content") if key in result
    ]
    if not set(result).issubset(allowed_result_keys) or len(structured_keys) != 1:
        raise _unsupported_result_shape(result)
    if "isError" in result and result.get("isError") is not False:
        raise HarnessContractError("Codex worker tool result reported an error")
    structured = result.get(structured_keys[0])
    if not isinstance(structured, Mapping):
        raise HarnessContractError("Codex worker structured result is missing")
    structured = dict(structured)
    operation = _operation_for_call(normalized, tool, arguments, structured)
    receipt: dict[str, Any] = {
        "schema_version": WORKER_TOOL_RECEIPT_VERSION,
        "sequence": _integer(sequence, "Codex worker receipt sequence", minimum=1),
        "server": "ur_worker",
        "tool": tool,
        "status": "completed",
        "operation_id": None if operation is None else operation["operation_id"],
        "arguments_hash": artifact_hash(arguments),
        "result_hash": artifact_hash(dict(result)),
    }

    if tool == "worker_read":
        structured = _exact_mapping(
            structured,
            {"operation_id", "path", "start_line", "end_line", "content", "sha256"},
            "worker_read structured result",
        )
        path = _authorized_path(operation, arguments.get("path"))
        if structured.get("path") != path or not isinstance(structured.get("content"), str):
            raise HarnessContractError("worker_read result does not match its sealed request")
        requested_start = _integer(arguments.get("start_line"), "worker_read start line", minimum=1)
        requested_end = _integer(arguments.get("end_line"), "worker_read end line", minimum=requested_start)
        returned_start = _integer(structured.get("start_line"), "worker_read returned start", minimum=1)
        returned_end = _integer(structured.get("end_line"), "worker_read returned end", minimum=returned_start)
        if returned_start != requested_start or returned_end > requested_end:
            raise HarnessContractError("worker_read returned range is outside its sealed request")
        receipt.update({
            "path": path,
            "requested_start_line": requested_start,
            "requested_end_line": requested_end,
            "returned_start_line": returned_start,
            "returned_end_line": returned_end,
            "source_sha256": _raw_sha256(structured.get("sha256"), "worker_read source hash"),
            "content_sha256": _stream_sha256(structured["content"]),
        })
    elif tool == "worker_search":
        structured = _exact_mapping(
            structured, {"operation_id", "matches", "truncated"},
            "worker_search structured result",
        )
        matches = structured.get("matches")
        if not isinstance(matches, list) or not isinstance(structured.get("truncated"), bool):
            raise HarnessContractError("worker_search result is malformed")
        for match in matches:
            match = _exact_mapping(match, {"path", "line", "text"}, "worker_search match")
            _authorized_path(operation, match.get("path"))
            _integer(match.get("line"), "worker_search match line", minimum=1)
            if not isinstance(match.get("text"), str):
                raise HarnessContractError("worker_search match text is malformed")
        receipt.update({"match_count": len(matches), "truncated": structured["truncated"]})
    elif tool == "worker_write":
        structured = _exact_mapping(
            structured, {"operation_id", "path", "sha256"},
            "worker_write structured result",
        )
        path = _authorized_path(operation, arguments.get("path"))
        if structured.get("path") != path:
            raise HarnessContractError("worker_write result path does not match its sealed request")
        receipt.update({
            "path": path,
            "output_sha256": _raw_sha256(structured.get("sha256"), "worker_write output hash"),
        })
    elif tool == "worker_execute":
        structured = _exact_mapping(
            structured,
            {"operation_id", "exit_code", "stdout", "stderr", "command_hash", "success"},
            "worker_execute structured result",
        )
        exit_code = _integer(structured.get("exit_code"), "worker_execute exit code")
        success = structured.get("success")
        if not isinstance(success, bool) or success != (exit_code == 0):
            raise HarnessContractError("worker_execute success state is inconsistent")
        if not isinstance(structured.get("stdout"), str) or not isinstance(structured.get("stderr"), str):
            raise HarnessContractError("worker_execute streams are malformed")
        command_hash = structured.get("command_hash")
        if not isinstance(command_hash, str) or not _SHA256.fullmatch(command_hash):
            raise HarnessContractError("worker_execute command hash is invalid")
        receipt.update({
            "exit_code": exit_code,
            "success": success,
            "command_hash": command_hash,
            "stdout_sha256": _stream_sha256(structured["stdout"]),
            "stderr_sha256": _stream_sha256(structured["stderr"]),
        })
    else:
        structured = _exact_mapping(
            structured,
            {
                "schema_version", "run_id", "run_plan_hash", "base_snapshot_hash",
                "files", "completed_operation_ids", "result_hash",
            },
            "worker_inventory structured result",
        )
        if (
            structured.get("schema_version") != "worker-result/1.0"
            or structured.get("run_id") != normalized["run_id"]
            or structured.get("run_plan_hash") != normalized["run_plan_hash"]
            or structured.get("base_snapshot_hash") != normalized["snapshot_hash"]
        ):
            raise HarnessContractError("worker_inventory result is not bound to the sealed plan")
        files = structured.get("files")
        completed_ids = structured.get("completed_operation_ids")
        if not isinstance(files, list) or not isinstance(completed_ids, list):
            raise HarnessContractError("worker_inventory result is malformed")
        for entry in files:
            entry = _exact_mapping(entry, {"path", "sha256", "size"}, "worker_inventory file")
            if not isinstance(entry.get("path"), str):
                raise HarnessContractError("worker_inventory file path is malformed")
            _raw_sha256(entry.get("sha256"), "worker_inventory file hash")
            _integer(entry.get("size"), "worker_inventory file size", minimum=0)
        expected_ids = {item["operation_id"] for item in normalized["operations"]}
        if any(not isinstance(value, str) or value not in expected_ids for value in completed_ids):
            raise HarnessContractError("worker_inventory completed operations are invalid")
        inventory_hash = structured.get("result_hash")
        if not isinstance(inventory_hash, str) or inventory_hash != artifact_hash({
            key: value for key, value in structured.items() if key != "result_hash"
        }):
            raise HarnessContractError("worker_inventory result hash is invalid")
        receipt.update({
            "file_count": len(files),
            "file_manifest_hash": artifact_hash(files),
            "inventory_result_hash": inventory_hash,
        })

    receipt["receipt_hash"] = artifact_hash(receipt)
    return receipt


def validate_worker_tool_receipts(
    value: object, plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate persisted bounded receipts without requiring discarded raw events."""
    normalized = validate_run_plan(plan)
    if not isinstance(value, list) or not value:
        raise HarnessContractError("Codex result omitted worker tool receipts")
    receipts: list[dict[str, Any]] = []
    observed_operations: set[str] = set()
    for sequence, candidate in enumerate(value, 1):
        if not isinstance(candidate, Mapping):
            raise HarnessContractError("Codex worker receipt must be an object")
        tool = candidate.get("tool")
        if tool not in WORKER_TOOLS:
            raise HarnessContractError("Codex worker receipt names an unknown tool")
        expected_keys = _COMMON_RECEIPT_KEYS | _TOOL_RECEIPT_KEYS[tool]
        if set(candidate) != expected_keys:
            raise HarnessContractError("Codex worker receipt has an unsupported shape")
        receipt = dict(candidate)
        if (
            receipt.get("schema_version") != WORKER_TOOL_RECEIPT_VERSION
            or receipt.get("sequence") != sequence
            or receipt.get("server") != "ur_worker"
            or receipt.get("status") != "completed"
            or receipt.get("receipt_hash") != artifact_hash({
                key: item for key, item in receipt.items() if key != "receipt_hash"
            })
        ):
            raise HarnessContractError("Codex worker receipt integrity check failed")
        for name in ("arguments_hash", "result_hash"):
            if not isinstance(receipt.get(name), str) or not _SHA256.fullmatch(receipt[name]):
                raise HarnessContractError("Codex worker receipt contains an invalid hash")
        operation_id = receipt.get("operation_id")
        if tool == "worker_inventory":
            if operation_id is not None:
                raise HarnessContractError("worker_inventory receipt cannot claim an operation")
        else:
            operation = next(
                (item for item in normalized["operations"] if item["operation_id"] == operation_id),
                None,
            )
            if operation is None or _TOOL_FOR_OPERATION_KIND.get(operation["kind"]) != tool:
                raise HarnessContractError("Codex worker receipt is outside the sealed plan")
            observed_operations.add(operation_id)
            if tool in {"worker_read", "worker_write"}:
                _authorized_path(operation, receipt.get("path"))
        if tool == "worker_read":
            requested_start = _integer(
                receipt.get("requested_start_line"), "worker_read receipt start", minimum=1,
            )
            requested_end = _integer(
                receipt.get("requested_end_line"), "worker_read receipt end", minimum=requested_start,
            )
            returned_start = _integer(
                receipt.get("returned_start_line"), "worker_read receipt returned start", minimum=1,
            )
            returned_end = _integer(
                receipt.get("returned_end_line"), "worker_read receipt returned end", minimum=returned_start,
            )
            if returned_start != requested_start or returned_end > requested_end:
                raise HarnessContractError("worker_read receipt range is invalid")
        elif tool == "worker_search":
            _integer(receipt.get("match_count"), "worker_search receipt match count", minimum=0)
            if not isinstance(receipt.get("truncated"), bool):
                raise HarnessContractError("worker_search receipt state is invalid")
        elif tool == "worker_execute":
            exit_code = _integer(receipt.get("exit_code"), "worker_execute receipt exit code")
            if not isinstance(receipt.get("success"), bool) or receipt["success"] != (exit_code == 0):
                raise HarnessContractError("worker_execute receipt state is inconsistent")
        elif tool == "worker_inventory":
            _integer(receipt.get("file_count"), "worker_inventory receipt file count", minimum=0)
        for name, item in receipt.items():
            if name.endswith("_hash") or name.endswith("_sha256"):
                if not isinstance(item, str) or not _SHA256.fullmatch(item):
                    raise HarnessContractError("Codex worker receipt contains an invalid SHA-256")
        receipts.append(receipt)
    expected_operations = {item["operation_id"] for item in normalized["operations"]}
    if observed_operations != expected_operations:
        raise HarnessContractError("Codex did not complete every sealed worker operation")
    return receipts


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
    worker_environment = worker_mcp_environment(project_root)
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
        "-c", "features.view_image=false",
        "-c", 'web_search="disabled"',
        "-c", 'mcp_servers.ur_worker.command="' + sys.executable.replace('"', '') + '"',
        "-c", "mcp_servers.ur_worker.args=" + json.dumps(args[1:]),
        *[
            item
            for name, value in sorted(worker_environment.items())
            for item in ("-c", f"mcp_servers.ur_worker.env.{name}={json.dumps(value)}")
        ],
        "-c", "mcp_servers.ur_worker.required=true",
        "-c", "mcp_servers.ur_worker.enabled_tools=" + json.dumps(WORKER_TOOLS),
        "-c", 'mcp_servers.ur_worker.default_tools_approval_mode="approve"',
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
            raise CodexWorkerProcessError(completed)
        events: list[dict[str, Any]] = []
        mcp_events: list[tuple[str, dict[str, Any]]] = []
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
                if item_type == "mcp_tool_call":
                    event_type = event.get("type")
                    if not isinstance(event_type, str):
                        raise HarnessContractError("Codex MCP event type is missing")
                    mcp_events.append((event_type, item))
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
            raise CodexTokenCeilingError(
                approved_max_total_tokens=normalized["resources"]["max_total_tokens"],
                usage=usage,
                events=events,
                final_text=final_text,
            )
        try:
            final = json.loads(final_text)
        except json.JSONDecodeError as exc:
            raise HarnessContractError("Codex final message is not structured JSON") from exc
        if not isinstance(final, dict) or not isinstance(final.get("segments"), list):
            raise HarnessContractError("Codex final output does not match the claim envelope")
        started: set[str] = set()
        completed_calls: set[str] = set()
        tool_receipts: list[dict[str, Any]] = []
        for event_type, item in mcp_events:
            if item.get("server") != "ur_worker":
                raise HarnessContractError("Codex used an unknown MCP server")
            if item.get("tool") not in WORKER_TOOLS:
                raise HarnessContractError("Codex used an unknown worker tool")
            call_id = item.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise HarnessContractError("Codex worker tool call ID is invalid")
            if event_type == "item.started":
                if call_id in started or call_id in completed_calls:
                    raise HarnessContractError("Codex emitted a duplicate worker call event")
                started.add(call_id)
                continue
            if event_type != "item.completed" or call_id in completed_calls:
                raise HarnessContractError("Codex emitted an unsupported worker call event")
            if started and call_id not in started:
                raise HarnessContractError("Codex completed an unstarted worker call")
            completed_calls.add(call_id)
            tool_receipts.append(build_worker_tool_receipt(
                item, normalized, sequence=len(tool_receipts) + 1,
            ))
        if started - completed_calls:
            raise HarnessContractError("Codex left a worker tool call incomplete")
        tool_receipts = validate_worker_tool_receipts(tool_receipts, normalized)
        return CodexResult(
            final_output=final,
            usage={**usage, "total_tokens": total},
            model=normalized["model"],
            events_hash=artifact_hash(events),
            tool_receipts=tool_receipts,
        )


def write_output_schema(path: str | Path) -> Path:
    candidate = Path(path)
    candidate.write_text(json.dumps(output_schema(), indent=2) + "\n", encoding="utf-8")
    return candidate
