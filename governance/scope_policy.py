"""Deterministic planning, estimate, and operation-scope policy for governance v2."""

from __future__ import annotations

from math import isfinite
from pathlib import PurePosixPath
from typing import Any

from governance.errors import COST_EXCEEDED, PLAN_REQUIRED, SCOPE_EXCEEDED, USER_OPT_IN_MISSING
from governance.hashing import artifact_hash


HOST_VISUALIZATION = "host_visualization"
DATA_PLOT_GENERATION = "data_plot_generation"
KNOWN_CAPABILITIES = frozenset({HOST_VISUALIZATION, DATA_PLOT_GENERATION})
NECESSITY_VALUES = frozenset({
    "required", "useful_but_not_required", "optional", "out_of_scope",
})
WORK_UNIT_FIELDS = (
    "files_to_read", "files_to_modify", "tests_to_run", "model_runs",
    "benchmark_runs",
)
NO_PLAN_ACTIONS = frozenset({
    "research_search", "research_fetch", "inspect_artifact", "emit_finding",
    "emit_decision", "assess_plan_necessity", "estimate_operation",
    "validate_operation_scope",
})


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _strings(value: Any) -> list[str]:
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def _path_is_within(candidate: str, allowed_root: str) -> bool:
    path = PurePosixPath(candidate)
    root = PurePosixPath(allowed_root)
    if path.is_absolute() or root.is_absolute() or ".." in path.parts or ".." in root.parts:
        return False
    return path == root or root in path.parents


def _nonnegative_number(value: Any, name: str) -> float | int | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{name} must be a non-negative number")
    return value


def _elapsed_range(operation: dict[str, Any]) -> dict[str, Any]:
    raw = operation.get("elapsed_time_minutes")
    if raw is None:
        return {
            "minimum_minutes": None,
            "likely_minutes": None,
            "maximum_minutes": None,
            "status": "unknown",
        }
    if not isinstance(raw, dict):
        raise ValueError("operation.elapsed_time_minutes must be an object")
    minimum = _nonnegative_number(raw.get("minimum"), "elapsed minimum")
    likely = _nonnegative_number(raw.get("likely"), "elapsed likely")
    maximum = _nonnegative_number(raw.get("maximum"), "elapsed maximum")
    if None in {minimum, likely, maximum} or not (minimum <= likely <= maximum):
        raise ValueError("elapsed range must satisfy 0 <= minimum <= likely <= maximum")
    return {
        "minimum_minutes": minimum,
        "likely_minutes": likely,
        "maximum_minutes": maximum,
        "status": "declared_range",
    }


def _work_units(operation: dict[str, Any]) -> dict[str, int]:
    raw = operation.get("work_units") or {}
    if not isinstance(raw, dict):
        raise ValueError("operation.work_units must be an object")
    normalized: dict[str, int] = {}
    for field in WORK_UNIT_FIELDS:
        value = raw.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"operation.work_units.{field} must be a non-negative integer")
        normalized[field] = value
    return normalized


def _difficulty(operation: dict[str, Any], units: dict[str, int]) -> str:
    if bool(operation.get("experimental")):
        return "experimental"
    score = (
        units["files_to_modify"] * 2
        + units["tests_to_run"]
        + units["model_runs"] * 4
        + units["benchmark_runs"] * 5
        + max(0, int(operation.get("parallelism", 1)) - 1) * 2
        + int(bool(operation.get("network"))) * 2
        + int(bool(operation.get("background"))) * 2
    )
    return "high" if score >= 15 else "medium" if score >= 5 else "low"


def task_scope_material(packet: dict[str, Any]) -> dict[str, Any]:
    """Return the exact immutable material bound by a task's scope hash."""

    authority = packet.get("authority") if isinstance(packet.get("authority"), dict) else {}
    failure = packet.get("failure_policy") if isinstance(packet.get("failure_policy"), dict) else {}
    return {
        "scope": packet.get("scope"),
        "evidence_boundary": packet.get("evidence_boundary"),
        "failure_policy": {
            "stop": failure.get("stop", "blocking_only"),
            "record": failure.get("record", "ask"),
            "detail": failure.get("detail", "redacted"),
        },
        "plan_refs": sorted(_strings(authority.get("plan_refs"))),
        "user_opt_ins": sorted(_strings(authority.get("user_opt_ins"))),
    }


def task_scope_hash(packet: dict[str, Any]) -> str:
    return artifact_hash(task_scope_material(packet))


def assess_plan_necessity(operation: dict[str, Any]) -> dict[str, Any]:
    """Return a stable plan decision and a bounded, declaration-based estimate."""

    if not isinstance(operation, dict) or not isinstance(operation.get("action"), str):
        raise ValueError("operation.action must be a non-empty string")
    action = operation["action"].strip()
    if not action:
        raise ValueError("operation.action must be a non-empty string")

    capabilities = set(_strings(operation.get("capabilities")))
    provider = str(operation.get("provider") or "local")
    parallelism = operation.get("parallelism", 1)
    if not isinstance(parallelism, int) or parallelism < 1:
        raise ValueError("operation.parallelism must be a positive integer")
    cost = operation.get("estimated_cost_usd")
    if cost is not None and (
        not isinstance(cost, (int, float))
        or isinstance(cost, bool)
        or not isfinite(float(cost))
        or cost < 0
    ):
        raise ValueError("operation.estimated_cost_usd must be non-negative")

    necessity = operation.get("necessity")
    if necessity is not None and necessity not in NECESSITY_VALUES:
        raise ValueError("operation.necessity is invalid")
    if necessity is None:
        necessity = (
            "out_of_scope" if bool(operation.get("declared_out_of_scope"))
            else "required" if operation.get("goal_requirement_ref")
            else "useful_but_not_required" if operation.get("benefit_ref")
            else "optional"
        )
    alternatives = _strings(operation.get("alternatives"))
    evidence_refs = operation.get("estimate_evidence_refs") or []
    if not isinstance(evidence_refs, list):
        raise ValueError("operation.estimate_evidence_refs must be an array")
    elapsed = _elapsed_range(operation)
    units = _work_units(operation)

    reasons: list[str] = []
    if action not in NO_PLAN_ACTIONS:
        reasons.append("action_requires_plan")
    if bool(operation.get("writes")):
        reasons.append("write_operation")
    if bool(operation.get("network")):
        reasons.append("network_operation")
    if bool(operation.get("background")):
        reasons.append("background_operation")
    if parallelism > 1:
        reasons.append("parallel_operation")
    if provider not in {"local", "host", "none"}:
        reasons.append("external_provider")
    if cost is not None and cost > 0:
        reasons.append("metered_cost")
    if HOST_VISUALIZATION in capabilities:
        reasons.append("host_visualization")
    if necessity == "out_of_scope":
        reasons.append("out_of_scope")

    external = provider not in {"local", "host", "none"}
    confidence = (
        "high" if evidence_refs and elapsed["status"] == "declared_range"
        else "medium" if evidence_refs or elapsed["status"] == "declared_range"
        else "low"
    )
    recommended = (
        "block" if necessity == "out_of_scope"
        else "approve_with_limits" if necessity == "required"
        else "request_user_decision"
    )
    return {
        "schema_version": "operation-plan-assessment/2.0",
        "plan_required": bool(reasons),
        "reasons": sorted(set(reasons)),
        "necessity": {
            "verdict": necessity,
            "reason": str(operation.get("necessity_reason") or "not supplied"),
            "alternatives": alternatives,
        },
        "estimate": {
            "elapsed_time_range": elapsed,
            "work_units": units,
            "provider": provider,
            "billing": "metered" if cost is not None and cost > 0 else "unknown" if external else "none",
            "estimated_cost_usd": cost,
            "cost_estimate_status": "declared" if cost is not None else "unknown" if external else "not_applicable",
            "parallelism": parallelism,
            "compute": "external" if external else "heavy" if parallelism > 1 or bool(operation.get("background")) else "light",
            "resource_cost": {
                "network_download_bytes": _nonnegative_number(
                    operation.get("network_download_bytes"), "network_download_bytes",
                ),
                "storage_bytes": _nonnegative_number(
                    operation.get("storage_bytes"), "storage_bytes",
                ),
                "paid_api_usage_usd": cost,
            },
            "difficulty": _difficulty(operation, units),
            "confidence": confidence,
            "evidence_refs": evidence_refs,
        },
        "recommended_decision": recommended,
    }


def validate_operation_scope(operation: dict[str, Any], packet: dict[str, Any]) -> list[dict[str, str]]:
    """Fail closed when one proposed operation exceeds a validated task packet."""

    if not isinstance(operation, dict) or not isinstance(packet, dict):
        return [_issue(SCOPE_EXCEEDED, "operation and task packet must be objects")]
    scope = packet.get("scope") or {}
    authority = packet.get("authority") or {}
    issues: list[dict[str, str]] = []
    action = operation.get("action")
    allowed_actions = set(_strings(scope.get("allowed_actions")))
    forbidden_actions = set(_strings(scope.get("forbidden_actions")))
    if action not in allowed_actions or action in forbidden_actions:
        issues.append(_issue(SCOPE_EXCEEDED, "operation action is outside task scope"))

    authority_scope_hash = authority.get("scope_hash")
    operation_scope_hash = operation.get("scope_hash")
    if authority_scope_hash:
        if authority_scope_hash != task_scope_hash(packet):
            issues.append(_issue(SCOPE_EXCEEDED, "task scope hash does not match its boundary"))
        if operation_scope_hash != authority_scope_hash:
            issues.append(_issue(SCOPE_EXCEEDED, "operation is not bound to the approved scope hash"))

    allowed_paths = _strings(scope.get("allowed_paths"))
    for path in _strings(operation.get("paths")):
        if not any(_path_is_within(path, root) for root in allowed_paths):
            issues.append(_issue(SCOPE_EXCEEDED, f"operation path is outside task scope: {path}"))
    allowed_sources = set(_strings(scope.get("allowed_sources")))
    for source in _strings(operation.get("sources")):
        if source not in allowed_sources:
            issues.append(_issue(SCOPE_EXCEEDED, f"operation source is outside task scope: {source}"))

    provider = operation.get("provider")
    if provider is not None and provider not in set(_strings(scope.get("allowed_providers"))):
        issues.append(_issue(SCOPE_EXCEEDED, "operation provider is outside task scope"))
    cost = operation.get("estimated_cost_usd")
    maximum = scope.get("max_cost_usd")
    if cost is not None:
        if (
            not isinstance(cost, (int, float))
            or isinstance(cost, bool)
            or not isfinite(float(cost))
            or cost < 0
        ):
            issues.append(_issue(COST_EXCEEDED, "operation cost estimate is invalid"))
        elif (
            not isinstance(maximum, (int, float))
            or isinstance(maximum, bool)
            or not isfinite(float(maximum))
            or cost > maximum
        ):
            issues.append(_issue(COST_EXCEEDED, "operation exceeds or lacks an explicit task cost ceiling"))

    boolean_boundaries = (
        ("network", "allow_network"),
        ("model_execution", "allow_model_execution"),
        ("benchmark", "allow_benchmark"),
        ("background", "allow_background"),
    )
    for operation_field, scope_field in boolean_boundaries:
        if bool(operation.get(operation_field)) and scope.get(scope_field) is not True:
            issues.append(_issue(SCOPE_EXCEEDED, f"operation {operation_field} is not approved"))
    parallelism = operation.get("parallelism", 1)
    maximum_parallelism = scope.get("max_parallelism", 1)
    if (
        not isinstance(parallelism, int) or isinstance(parallelism, bool) or parallelism < 1
        or not isinstance(maximum_parallelism, int) or isinstance(maximum_parallelism, bool)
        or maximum_parallelism < 1 or parallelism > maximum_parallelism
    ):
        issues.append(_issue(COST_EXCEEDED, "operation parallelism exceeds or lacks an explicit ceiling"))

    capabilities = set(_strings(operation.get("capabilities")))
    allowed_capabilities = set(_strings(scope.get("allowed_capabilities")))
    for capability in sorted(capabilities - allowed_capabilities):
        issues.append(_issue(SCOPE_EXCEEDED, f"operation capability is outside task scope: {capability}"))
    if HOST_VISUALIZATION in capabilities:
        opt_ins = set(_strings(authority.get("user_opt_ins")))
        if HOST_VISUALIZATION not in opt_ins:
            issues.append(_issue(USER_OPT_IN_MISSING, "host visualization requires explicit user opt-in"))
    # Generating a data plot is deliberately independent of invoking a host
    # visualization skill; one capability never grants the other.

    try:
        assessment = assess_plan_necessity(operation)
    except ValueError as exc:
        issues.append(_issue(SCOPE_EXCEEDED, str(exc)))
        return issues
    if assessment["plan_required"] and not _strings(authority.get("plan_refs")):
        issues.append(_issue(PLAN_REQUIRED, "operation requires a recorded plan reference"))
    return issues


def operation_gate(operation: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Return the controller action; this function never executes the operation."""

    issues = validate_operation_scope(operation, packet)
    return {
        "schema_version": "operation-gate/2.0",
        "allowed": not issues,
        "controller_action": "allow_tool_call" if not issues else "reject_tool_call",
        "workflow_state": "approved_scope" if not issues else "blocked",
        "issues": issues,
        "reapproval_required": bool(issues),
    }
