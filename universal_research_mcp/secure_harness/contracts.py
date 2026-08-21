"""Closed contracts for the Codex/Docker research harness.

The plan deliberately contains no generic shell string or raw Docker options.
Every executable operation is represented as an argv vector and is sealed by
the plan hash before a one-time approval can be created.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from universal_research_mcp.governance.agent_creation import (
    AgentCreationDisclosureError,
    normalize_agent_creation_disclosure,
)
from universal_research_mcp.governance.hashing import hash_without


RUN_PLAN_VERSION = "research-run-plan/2.0"
OPERATION_VERSION = "worker-operation/1.0"
CLAIM_VERSION = "claim-inventory/1.0"
APPROVAL_MODES = frozenset({"plan_once", "sensitive_stage", "each_operation"})
VERIFICATION_MODES = frozenset({"adaptive", "strict"})
WORKFLOW_MODES = frozenset({"lightweight", "benchmark", "final_review"})
OPERATION_KINDS = frozenset({"read", "search", "patch", "test", "build", "experiment"})
CLAIM_LEVELS = frozenset({"L0", "L1", "L2", "L3"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_FORBIDDEN_PATH_PARTS = frozenset({".git", ".codex", ".agents"})
_FORBIDDEN_EXECUTABLES = frozenset({
    "sh", "bash", "dash", "zsh", "fish", "pwsh", "powershell", "cmd",
    "docker", "podman", "sudo", "su",
})


class HarnessContractError(ValueError):
    """Raised before execution when a harness contract is not exact."""


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or ".." in value:
        raise HarnessContractError(f"{label} is invalid")
    return value


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HarnessContractError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HarnessContractError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise HarnessContractError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HarnessContractError(f"{label} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(part in _FORBIDDEN_PATH_PARTS for part in path.parts):
        raise HarnessContractError(f"{label} escapes or enters a protected path")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise HarnessContractError(f"{label} cannot be the project root")
    return normalized


def _cwd(value: object) -> str:
    if value == ".":
        return "."
    return _path(value, "operation cwd")


def _positive_int(value: object, label: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > maximum:
        raise HarnessContractError(f"{label} must be in [1, {maximum}]")
    return value


def _operation(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessContractError("operation must be an object")
    allowed = {
        "schema_version", "operation_id", "kind", "paths", "argv", "cwd",
        "environment", "timeout_seconds", "network", "gpu_devices",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HarnessContractError(f"operation contains unsupported fields: {', '.join(unknown)}")
    if value.get("schema_version") != OPERATION_VERSION:
        raise HarnessContractError("unsupported worker operation schema")
    operation_id = _identifier(value.get("operation_id"), "operation_id")
    kind = value.get("kind")
    if kind not in OPERATION_KINDS:
        raise HarnessContractError("operation kind is unsupported")
    paths = value.get("paths")
    if not isinstance(paths, list) or not paths:
        raise HarnessContractError("operation paths must be a non-empty array")
    normalized_paths = [_path(item, "operation path") for item in paths]
    if len(normalized_paths) != len(set(normalized_paths)):
        raise HarnessContractError("operation paths must be unique")
    argv = value.get("argv", [])
    if not isinstance(argv, list) or any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise HarnessContractError("operation argv must be an array of non-empty strings")
    if kind in {"test", "build", "experiment"} and not argv:
        raise HarnessContractError(f"{kind} operation requires an exact argv vector")
    if argv:
        executable = PurePosixPath(argv[0].replace("\\", "/")).name.lower()
        if executable in _FORBIDDEN_EXECUTABLES:
            raise HarnessContractError("operation cannot invoke a shell or privilege/container frontend")
        if executable.startswith("python") and any(item in {"-c", "-"} for item in argv[1:]):
            raise HarnessContractError("operation cannot execute inline interpreter source")
        if executable in {"node", "deno", "ruby", "perl"} and any(item in {"-e", "--eval"} for item in argv[1:]):
            raise HarnessContractError("operation cannot execute inline interpreter source")
    if kind in {"read", "search", "patch"} and argv:
        raise HarnessContractError(f"{kind} operation cannot carry argv")
    cwd = _cwd(value.get("cwd", "."))
    environment = value.get("environment", {})
    if not isinstance(environment, Mapping) or any(
        not isinstance(name, str) or not _ENV_NAME.fullmatch(name)
        or not isinstance(item, str) or "\x00" in item
        for name, item in environment.items()
    ):
        raise HarnessContractError("operation environment must contain safe names and string values")
    if any(name.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")) for name in environment):
        raise HarnessContractError("operation environment cannot contain credential-like values")
    timeout = _positive_int(value.get("timeout_seconds", 1800), "operation timeout", 86_400)
    if value.get("network", False) is not False:
        raise HarnessContractError("execution workers cannot request network access")
    gpu_devices = value.get("gpu_devices", [])
    if not isinstance(gpu_devices, list) or any(
        not isinstance(item, str) or not re.fullmatch(r"GPU-[0-9a-fA-F-]{8,64}", item)
        for item in gpu_devices
    ):
        raise HarnessContractError("gpu_devices must contain exact GPU UUIDs")
    if gpu_devices and kind != "experiment":
        raise HarnessContractError("GPU access is restricted to experiment operations")
    return {
        "schema_version": OPERATION_VERSION,
        "operation_id": operation_id,
        "kind": kind,
        "paths": normalized_paths,
        "argv": list(argv),
        "cwd": cwd,
        "environment": dict(sorted(environment.items())),
        "timeout_seconds": timeout,
        "network": False,
        "gpu_devices": list(gpu_devices),
    }


def _resources(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessContractError("resources must be an object")
    allowed = {"cpus", "memory_mb", "pids", "max_parallelism", "max_total_tokens", "max_cost_usd"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HarnessContractError(f"resources contains unsupported fields: {', '.join(unknown)}")
    cpus = value.get("cpus", 4)
    if not isinstance(cpus, (int, float)) or isinstance(cpus, bool) or not isfinite(float(cpus)) or cpus <= 0 or cpus > 64:
        raise HarnessContractError("resources.cpus must be in (0, 64]")
    cost = value.get("max_cost_usd", 0)
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not isfinite(float(cost)) or cost < 0:
        raise HarnessContractError("resources.max_cost_usd must be non-negative")
    return {
        "cpus": float(cpus),
        "memory_mb": _positive_int(value.get("memory_mb", 4096), "resources.memory_mb", 262_144),
        "pids": _positive_int(value.get("pids", 256), "resources.pids", 32_768),
        "max_parallelism": _positive_int(value.get("max_parallelism", 1), "resources.max_parallelism", 16),
        "max_total_tokens": _positive_int(value.get("max_total_tokens", 200_000), "resources.max_total_tokens", 10_000_000),
        "max_cost_usd": float(cost),
    }


def validate_run_plan(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessContractError("run plan must be an object")
    allowed = {
        "schema_version", "run_id", "workflow_id", "project_root_hash", "model",
        "reasoning_effort", "workflow_mode", "verification_mode", "approval_mode", "image",
        "snapshot_hash", "resources", "operations", "created_at", "expires_at",
        "agent_creation_disclosure", "run_plan_hash",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HarnessContractError(f"run plan contains unsupported fields: {', '.join(unknown)}")
    if value.get("schema_version") != RUN_PLAN_VERSION:
        raise HarnessContractError("unsupported run plan schema")
    project_root_hash = value.get("project_root_hash")
    snapshot_hash = value.get("snapshot_hash")
    if not isinstance(project_root_hash, str) or not _SHA256.fullmatch(project_root_hash):
        raise HarnessContractError("project_root_hash must be an exact SHA-256")
    if not isinstance(snapshot_hash, str) or not _SHA256.fullmatch(snapshot_hash):
        raise HarnessContractError("snapshot_hash must be an exact SHA-256")
    image = value.get("image")
    if not isinstance(image, str) or not _IMAGE_DIGEST.fullmatch(image):
        raise HarnessContractError("image must be pinned by sha256 digest")
    model = value.get("model")
    reasoning = value.get("reasoning_effort")
    if not isinstance(model, str) or not model.strip():
        raise HarnessContractError("model must be explicit")
    if reasoning not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
        raise HarnessContractError("reasoning_effort is unsupported")
    verification = value.get("verification_mode")
    approval = value.get("approval_mode")
    workflow_mode = value.get("workflow_mode", "lightweight")
    if workflow_mode not in WORKFLOW_MODES:
        raise HarnessContractError("workflow_mode is unsupported")
    if verification not in VERIFICATION_MODES:
        raise HarnessContractError("verification_mode is unsupported")
    if workflow_mode in {"benchmark", "final_review"} and verification != "strict":
        raise HarnessContractError("benchmark and final_review plans require strict verification")
    if approval not in APPROVAL_MODES:
        raise HarnessContractError("approval_mode is unsupported")
    operations = value.get("operations")
    if not isinstance(operations, list) or not operations:
        raise HarnessContractError("run plan requires operations")
    normalized_operations = [_operation(item) for item in operations]
    identities = [item["operation_id"] for item in normalized_operations]
    if len(identities) != len(set(identities)):
        raise HarnessContractError("operation IDs must be unique")
    try:
        disclosure = normalize_agent_creation_disclosure(
            value.get("agent_creation_disclosure"),
            expected_agent_count=1,
        )
    except AgentCreationDisclosureError as exc:
        raise HarnessContractError(str(exc)) from exc
    disclosed_scope = disclosure["scope"]
    expected_paths = sorted({
        path for operation in normalized_operations for path in operation["paths"]
    })
    expected_writes = any(
        operation["kind"] in {"patch", "test", "build", "experiment"}
        for operation in normalized_operations
    )
    if sorted(disclosed_scope["paths"]) != expected_paths:
        raise HarnessContractError("agent creation disclosure paths do not match the plan")
    if disclosed_scope["network"] is not False:
        raise HarnessContractError("agent creation disclosure cannot grant worker network")
    if disclosed_scope["writes"] is not expected_writes:
        raise HarnessContractError("agent creation disclosure write scope does not match the plan")
    normalized = {
        "schema_version": RUN_PLAN_VERSION,
        "run_id": _identifier(value.get("run_id"), "run_id"),
        "workflow_id": _identifier(value.get("workflow_id"), "workflow_id"),
        "project_root_hash": project_root_hash,
        "model": model,
        "reasoning_effort": reasoning,
        "workflow_mode": workflow_mode,
        "verification_mode": verification,
        "approval_mode": approval,
        "agent_creation_disclosure": disclosure,
        "image": image,
        "snapshot_hash": snapshot_hash,
        "resources": _resources(value.get("resources")),
        "operations": normalized_operations,
        "created_at": _timestamp(value.get("created_at"), "created_at"),
        "expires_at": _timestamp(value.get("expires_at"), "expires_at"),
    }
    created = datetime.fromisoformat(normalized["created_at"])
    expires = datetime.fromisoformat(normalized["expires_at"])
    if expires <= created:
        raise HarnessContractError("run plan must expire after creation")
    material = {**normalized, "run_plan_hash": None}
    computed = hash_without(material, "run_plan_hash")
    supplied = value.get("run_plan_hash")
    if supplied is not None and supplied != computed:
        raise HarnessContractError("run plan hash mismatch")
    normalized["run_plan_hash"] = computed
    return normalized


def build_run_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    material = dict(value)
    material.pop("run_plan_hash", None)
    return validate_run_plan(material)


def load_run_plan(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 1024 * 1024:
        raise HarnessContractError("run plan must be a regular file no larger than 1 MiB")
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessContractError("run plan is not readable JSON") from exc
    return validate_run_plan(value)


def classify_claim(value: Mapping[str, Any], *, verification_mode: str = "adaptive") -> dict[str, Any]:
    """Classify one claim using deterministic, domain-neutral signals."""

    if verification_mode not in VERIFICATION_MODES:
        raise HarnessContractError("verification_mode is unsupported")
    allowed = {
        "claim_id", "statement", "kind", "final", "external", "numerical",
        "citation", "benchmark", "causal", "canonical", "conflicting",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HarnessContractError(f"claim contains unsupported fields: {', '.join(unknown)}")
    claim_id = _identifier(value.get("claim_id"), "claim_id")
    statement = value.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        raise HarnessContractError("claim statement is required")
    kind = value.get("kind")
    if kind not in {"creative", "interpretation", "factual", "recommendation", "result"}:
        raise HarnessContractError("claim kind is unsupported")
    flags = {name: value.get(name, False) for name in allowed - {"claim_id", "statement", "kind"}}
    if any(not isinstance(item, bool) for item in flags.values()):
        raise HarnessContractError("claim flags must be boolean")
    if flags["benchmark"] or flags["causal"] or flags["canonical"] or flags["conflicting"]:
        level = "L3"
    elif flags["final"] and (kind in {"factual", "result"} or flags["external"] or flags["numerical"] or flags["citation"]):
        level = "L2"
    elif kind in {"factual", "result"} or flags["external"]:
        level = "L1"
    else:
        level = "L0"
    if verification_mode == "strict" and level == "L1":
        level = "L2"
    return {
        "schema_version": CLAIM_VERSION,
        "claim_id": claim_id,
        "level": level,
        "retrieval_required": level in {"L1", "L2", "L3"},
        "source_verification_required": level in {"L2", "L3"},
        "independent_review_required": level == "L3",
        "claim_eligible": level == "L0",
    }
