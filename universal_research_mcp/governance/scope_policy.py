"""Deterministic planning, estimate, and operation-scope policy for governance v2."""

from __future__ import annotations

from math import isfinite
from pathlib import PurePosixPath
from typing import Any

from universal_research_mcp.governance.errors import COST_EXCEEDED, PLAN_REQUIRED, SCOPE_EXCEEDED, USER_OPT_IN_MISSING
from universal_research_mcp.governance.hashing import artifact_hash


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
WRITE_ACTIONS = frozenset({
    "edit_derived_artifact", "rebuild_derived_index", "repair_index",
})

# The gate is an authority boundary, so its input contract is deliberately
# closed. Unknown fields are not harmless metadata: an execution adapter could
# otherwise interpret values such as ``command``, ``args``, or ``env`` after
# this validator has approved the surrounding operation.
OPERATION_FIELDS = frozenset({
    "action",
    "alternatives",
    "background",
    "benchmark",
    "benefit_ref",
    "capabilities",
    "declared_out_of_scope",
    "elapsed_time_minutes",
    "estimate_evidence_refs",
    "estimated_cost_usd",
    "experimental",
    "goal_requirement_ref",
    "model_execution",
    "necessity",
    "necessity_reason",
    "network",
    "network_download_bytes",
    "parallelism",
    "paths",
    "provider",
    "scope_hash",
    "sources",
    "storage_bytes",
    "work_units",
    "writes",
})


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _strings(value: Any) -> list[str]:
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def _operation_contract_errors(operation: Any) -> list[str]:
    """Validate the closed, typed operation envelope without granting scope."""

    if not isinstance(operation, dict):
        return ["operation must be an object"]
    errors: list[str] = []
    unknown_fields = sorted(str(field) for field in set(operation) - OPERATION_FIELDS)
    if unknown_fields:
        errors.append(f"operation contains unsupported fields: {', '.join(unknown_fields)}")
    action = operation.get("action")
    if not isinstance(action, str) or not action.strip():
        errors.append("operation.action must be a non-empty string")
    for field in ("paths", "sources", "capabilities", "alternatives", "estimate_evidence_refs"):
        value = operation.get(field)
        if value is not None and (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
        ):
            errors.append(f"operation.{field} must be an array of non-empty strings")
    for field in (
        "background", "benchmark", "declared_out_of_scope", "experimental",
        "model_execution", "network", "writes",
    ):
        if field in operation and not isinstance(operation[field], bool):
            errors.append(f"operation.{field} must be boolean")
    for field in ("benefit_ref", "goal_requirement_ref", "necessity_reason", "provider"):
        if field in operation and (
            not isinstance(operation[field], str) or not operation[field].strip()
        ):
            errors.append(f"operation.{field} must be a non-empty string")
    scope_hash = operation.get("scope_hash")
    if "scope_hash" in operation and not (
        isinstance(scope_hash, str)
        and scope_hash.startswith("sha256:")
        and len(scope_hash) == 71
        and all(character in "0123456789abcdef" for character in scope_hash[7:])
    ):
        errors.append("operation.scope_hash must be an exact sha256 artifact hash")
    if "necessity" in operation and (
        not isinstance(operation["necessity"], str)
        or operation["necessity"] not in NECESSITY_VALUES
    ):
        errors.append("operation.necessity is invalid")
    parallelism = operation.get("parallelism")
    if parallelism is not None and (
        not isinstance(parallelism, int) or isinstance(parallelism, bool) or parallelism < 1
    ):
        errors.append("operation.parallelism must be a positive integer")
    for field in ("estimated_cost_usd", "network_download_bytes", "storage_bytes"):
        value = operation.get(field)
        if value is not None and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(float(value))
            or value < 0
        ):
            errors.append(f"operation.{field} must be a non-negative finite number")
    elapsed = operation.get("elapsed_time_minutes")
    if elapsed is not None:
        if not isinstance(elapsed, dict):
            errors.append("operation.elapsed_time_minutes must be an object")
        else:
            unknown = sorted(
                str(field) for field in set(elapsed) - {"minimum", "likely", "maximum"}
            )
            if unknown:
                errors.append(
                    "operation.elapsed_time_minutes contains unsupported fields: "
                    + ", ".join(unknown)
                )
    work_units = operation.get("work_units")
    if work_units is not None:
        if not isinstance(work_units, dict):
            errors.append("operation.work_units must be an object")
        else:
            unknown = sorted(
                str(field) for field in set(work_units) - set(WORK_UNIT_FIELDS)
            )
            if unknown:
                errors.append(
                    "operation.work_units contains unsupported fields: "
                    + ", ".join(unknown)
                )
    return errors


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
    if minimum is None or likely is None or maximum is None or not (minimum <= likely <= maximum):
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

    raw_authority = packet.get("authority")
    raw_failure = packet.get("failure_policy")
    authority = raw_authority if isinstance(raw_authority, dict) else {}
    failure = raw_failure if isinstance(raw_failure, dict) else {}
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

    contract_errors = _operation_contract_errors(operation)
    if contract_errors:
        raise ValueError("; ".join(contract_errors))
    action = operation["action"].strip()

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
    raw_scope = packet.get("scope")
    raw_authority = packet.get("authority")
    scope = raw_scope if isinstance(raw_scope, dict) else {}
    authority = raw_authority if isinstance(raw_authority, dict) else {}
    issues: list[dict[str, str]] = []
    contract_errors = _operation_contract_errors(operation)
    issues.extend(_issue(SCOPE_EXCEEDED, message) for message in contract_errors)
    if contract_errors:
        return issues
    action = operation.get("action")
    if action in {"inspect_artifact", "research_fetch"} and not _strings(operation.get("paths")):
        issues.append(_issue(SCOPE_EXCEEDED, f"operation.{action} requires at least one path"))
    allowed_actions = set(_strings(scope.get("allowed_actions")))
    forbidden_actions = set(_strings(scope.get("forbidden_actions")))
    if action not in allowed_actions or action in forbidden_actions:
        issues.append(_issue(SCOPE_EXCEEDED, "operation action is outside task scope"))
    if bool(operation.get("writes")) != (action in WRITE_ACTIONS):
        issues.append(_issue(
            SCOPE_EXCEEDED,
            "operation write intent does not match an approved derived-write action",
        ))
    if action in WRITE_ACTIONS and not _strings(operation.get("paths")):
        issues.append(_issue(
            SCOPE_EXCEEDED,
            "derived-write operation requires at least one explicit target path",
        ))

    authority_scope_hash = authority.get("scope_hash")
    operation_scope_hash = operation.get("scope_hash")
    try:
        computed_scope_hash = task_scope_hash(packet)
    except (TypeError, ValueError):
        computed_scope_hash = None
    if authority_scope_hash != computed_scope_hash or computed_scope_hash is None:
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
    if assessment["recommended_decision"] == "block":
        issues.append(_issue(SCOPE_EXCEEDED, "operation is explicitly classified out of scope"))
    if bool(operation.get("network")) or bool(operation.get("model_execution")):
        if not isinstance(operation.get("provider"), str):
            issues.append(_issue(SCOPE_EXCEEDED, "network or model execution requires an explicit provider"))
        if "estimated_cost_usd" not in operation:
            issues.append(_issue(COST_EXCEEDED, "network or model execution requires an explicit cost estimate"))
    if assessment["plan_required"] and not _strings(authority.get("plan_refs")):
        issues.append(_issue(PLAN_REQUIRED, "operation requires a recorded plan reference"))
    return issues


def operation_gate(operation: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Return the controller action; this function never executes the operation."""

    # Import lazily because the task validator itself imports this module for
    # scope hashing. The execution gate, unlike the lower-level scope helper,
    # requires a complete valid task packet.
    from universal_research_mcp.governance.validation import validate_task_packet

    try:
        packet_issues = validate_task_packet(packet)
    except (AttributeError, TypeError, ValueError) as exc:
        packet_issues = [_issue(
            SCOPE_EXCEEDED,
            f"task packet validation failed closed: {type(exc).__name__}",
        )]
    issues = [*packet_issues, *validate_operation_scope(operation, packet)]
    try:
        operation_hash = artifact_hash(operation)
    except (TypeError, ValueError):
        operation_hash = None
        issues.append(_issue(
            SCOPE_EXCEEDED,
            "operation cannot be bound to a canonical artifact hash",
        ))
    try:
        packet_hash = artifact_hash(packet)
    except (TypeError, ValueError):
        packet_hash = None
        issues.append(_issue(
            SCOPE_EXCEEDED,
            "task packet cannot be bound to a canonical artifact hash",
        ))
    preflight_passed = not issues
    return {
        "schema_version": "operation-gate/3.0",
        "preflight_passed": preflight_passed,
        "controller_action": "preflight_passed" if preflight_passed else "preflight_blocked",
        "execution_authorized": False,
        "host_argument_binding_required": preflight_passed,
        "workflow_state": "preflight_complete" if preflight_passed else "blocked",
        "issues": issues,
        "reapproval_required": bool(issues),
        "operation_hash": operation_hash,
        "task_packet_hash": packet_hash,
        "scope_hash": (
            packet["authority"].get("scope_hash")
            if isinstance(packet, dict) and isinstance(packet.get("authority"), dict)
            else None
        ),
    }
