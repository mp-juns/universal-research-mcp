"""Fail-closed URAG task and decision validation without model execution."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

from universal_research_mcp.governance.errors import APPROVAL_MISSING, EVIDENCE_MISSING, OUTPUT_INVALID, REGISTRY_UNKNOWN_AGENT, SCOPE_EXCEEDED
from universal_research_mcp.governance.hashing import artifact_hash, hash_without
from universal_research_mcp.governance.registry import CRITICAL, load_registry, manifest_hash
from universal_research_mcp.governance.failure_policy import resolve_failure_policy
from universal_research_mcp.governance.scope_policy import HOST_VISUALIZATION, KNOWN_CAPABILITIES, task_scope_hash


WRITE_ACTIONS = frozenset({"edit_derived_artifact", "rebuild_derived_index", "repair_index"})
_GOVERNOR_NECESSITY = frozenset({
    "required", "useful_but_not_required", "optional", "out_of_scope",
})
_GOVERNOR_DIFFICULTY = frozenset({"low", "medium", "high", "experimental"})
_GOVERNOR_CONFIDENCE = frozenset({"high", "medium", "low"})
_GOVERNOR_SCOPE = frozenset({"within_approved_scope", "reapproval_required", "blocked"})
_UNBOUNDED_MARKERS = frozenset({
    "", "?", "n/a", "na", "none", "not applicable", "not_applicable",
    "not provided", "pending", "tbd", "to be determined", "unbounded",
    "unknown", "unset",
})


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _nonempty_strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _bounded_value(value: Any) -> bool:
    """Return whether one estimate value is explicit, finite, and bounded."""

    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return isfinite(float(value)) and value >= 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _UNBOUNDED_MARKERS:
            return False
        return not any(
            marker in normalized
            for marker in (
                "unknown", "unbounded", "pending", "not provided", "unset",
                "to be determined",
            )
        )
    if isinstance(value, dict):
        return bool(value) and all(
            isinstance(key, str) and key and _bounded_value(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return bool(value) and all(_bounded_value(item) for item in value)
    return False


def validate_scope_governor_decision(
    decision: dict[str, Any],
    *,
    expected_plan_hash: str | None = None,
) -> list[dict[str, str]]:
    """Validate the deterministic cross-field contract for the scope governor.

    A model may describe a plan as passing only when the plan is within scope,
    no required follow-up or user choice remains, and one explicit bounded
    estimate is present.  This validator intentionally does not grant approval;
    it only validates a finding that a separate controller may bind.
    """

    issues: list[dict[str, str]] = []
    if not isinstance(decision, dict):
        return [_issue(OUTPUT_INVALID, "scope governor decision must be an object")]
    if decision.get("agent_id") != "scope_and_cost_governor":
        return [_issue(OUTPUT_INVALID, "scope governor validator received another role")]

    classification = decision.get("classification")
    if not isinstance(classification, dict):
        return [_issue(OUTPUT_INVALID, "scope governor classification must be an object")]
    enum_fields = {
        "necessity_verdict": _GOVERNOR_NECESSITY,
        "difficulty": _GOVERNOR_DIFFICULTY,
        "estimate_confidence": _GOVERNOR_CONFIDENCE,
        "scope_verdict": _GOVERNOR_SCOPE,
        "additional_work": _GOVERNOR_NECESSITY,
    }
    for field, allowed in enum_fields.items():
        if classification.get(field) not in allowed:
            issues.append(_issue(OUTPUT_INVALID, f"scope governor {field} is invalid"))

    reviewed_plan_hash = classification.get("reviewed_plan_hash")
    if (
        not isinstance(reviewed_plan_hash, str)
        or not reviewed_plan_hash.startswith("sha256:")
        or len(reviewed_plan_hash) != 71
        or any(character not in "0123456789abcdef" for character in reviewed_plan_hash[7:])
    ):
        issues.append(_issue(OUTPUT_INVALID, "scope governor reviewed_plan_hash is invalid"))
    elif expected_plan_hash is not None and reviewed_plan_hash != expected_plan_hash:
        issues.append(_issue(SCOPE_EXCEEDED, "scope governor reviewed_plan_hash mismatch"))

    estimate_fields = {
        "elapsed_time_range", "work_units", "resource_cost", "assumptions",
        "evidence_refs", "user_choice_required",
    }
    estimate_candidates = [
        item for item in (decision.get("decisions") or [])
        if isinstance(item, dict) and estimate_fields & set(item)
    ]
    if len(estimate_candidates) != 1:
        issues.append(_issue(
            OUTPUT_INVALID,
            "scope governor requires exactly one unambiguous bounded estimate decision",
        ))
        estimate: dict[str, Any] | None = None
    else:
        estimate = estimate_candidates[0]
        if not estimate_fields <= set(estimate):
            issues.append(_issue(OUTPUT_INVALID, "scope governor estimate fields are incomplete"))
        elapsed = estimate.get("elapsed_time_range")
        if (
            not isinstance(elapsed, dict)
            or not {"minimum", "likely", "maximum"} <= set(elapsed)
            or not all(_bounded_value(elapsed.get(field)) for field in ("minimum", "likely", "maximum"))
        ):
            issues.append(_issue(OUTPUT_INVALID, "scope governor elapsed-time range is not bounded"))
        for field in ("work_units", "resource_cost"):
            if not isinstance(estimate.get(field), dict) or not _bounded_value(estimate.get(field)):
                issues.append(_issue(OUTPUT_INVALID, f"scope governor {field} is not bounded"))
        assumptions = estimate.get("assumptions")
        if not isinstance(assumptions, list) or not assumptions or not all(
            isinstance(value, str) and value.strip() for value in assumptions
        ):
            issues.append(_issue(OUTPUT_INVALID, "scope governor estimate assumptions are missing"))
        evidence_refs = estimate.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            issues.append(_issue(EVIDENCE_MISSING, "scope governor estimate evidence is missing"))
        if not isinstance(estimate.get("user_choice_required"), bool):
            issues.append(_issue(OUTPUT_INVALID, "scope governor user_choice_required must be boolean"))

    if decision.get("status") == "pass":
        if classification.get("necessity_verdict") not in {
            "required", "useful_but_not_required", "optional",
        }:
            issues.append(_issue(SCOPE_EXCEEDED, "scope governor pass cannot classify the plan out_of_scope"))
        if classification.get("scope_verdict") != "within_approved_scope":
            issues.append(_issue(SCOPE_EXCEEDED, "scope governor pass requires within_approved_scope"))
        if classification.get("additional_work") in {"required", "out_of_scope"}:
            issues.append(_issue(
                APPROVAL_MISSING,
                "scope governor pass cannot leave required or out-of-scope additional work",
            ))
        if estimate is not None and estimate.get("user_choice_required") is True:
            issues.append(_issue(APPROVAL_MISSING, "scope governor pass cannot require a user choice"))
    return issues


def validate_task_packet(packet: dict[str, Any], registry: dict[str, dict[str, Any]] | None = None, now: datetime | None = None) -> list[dict[str, str]]:
    registry = registry or load_registry()
    issues: list[dict[str, str]] = []
    required = {"schema_version", "governance_version", "run_id", "workflow_id", "agent_id", "requester", "purpose", "mode", "scope", "evidence_boundary", "authority", "failure_policy", "success_criteria", "stop_conditions", "role_manifest_hash", "created_at", "expires_at"}
    missing = required - set(packet) if isinstance(packet, dict) else required
    if missing:
        return [_issue(OUTPUT_INVALID, f"task packet is missing: {', '.join(sorted(missing))}")]
    if packet.get("schema_version") != "research-agent-task/1.0":
        issues.append(_issue(OUTPUT_INVALID, "unsupported task packet schema"))
    if packet.get("governance_version") != "agent-governance/2.0":
        issues.append(_issue(OUTPUT_INVALID, "task packet must bind agent-governance/2.0"))
    agent_id = packet.get("agent_id")
    if not isinstance(agent_id, str):
        return [_issue(REGISTRY_UNKNOWN_AGENT, "task packet agent_id must be a string")]
    manifest = registry.get(agent_id)
    if manifest is None:
        return [_issue(REGISTRY_UNKNOWN_AGENT, "unknown governance agent")]
    if packet.get("role_manifest_hash") != manifest_hash(manifest):
        issues.append(_issue(OUTPUT_INVALID, "role manifest hash mismatch"))
    if packet.get("mode") not in manifest.get("activation", {}).get("modes", []):
        issues.append(_issue(SCOPE_EXCEEDED, "role is not active in the requested workflow mode"))
    requester = packet.get("requester")
    if not isinstance(requester, dict) or requester.get("type") not in {"user", "host_agent", "workflow"} or not isinstance(requester.get("id"), str):
        issues.append(_issue(OUTPUT_INVALID, "invalid requester"))
    scope = packet.get("scope")
    if not isinstance(scope, dict):
        issues.append(_issue(OUTPUT_INVALID, "scope must be an object"))
    else:
        for key in ("allowed_paths", "allowed_sources", "allowed_actions", "forbidden_actions"):
            if not _nonempty_strings(scope.get(key)):
                issues.append(_issue(OUTPUT_INVALID, f"scope.{key} must be a string array"))
        for key in ("allowed_capabilities", "allowed_providers"):
            if key not in scope or not _nonempty_strings(scope.get(key)):
                issues.append(_issue(OUTPUT_INVALID, f"scope.{key} must be a string array"))
        for key in ("allow_network", "allow_model_execution", "allow_benchmark", "allow_background"):
            if not isinstance(scope.get(key), bool):
                issues.append(_issue(OUTPUT_INVALID, f"scope.{key} must be boolean"))
        maximum_parallelism = scope.get("max_parallelism")
        if (
            not isinstance(maximum_parallelism, int) or isinstance(maximum_parallelism, bool)
            or maximum_parallelism < 1
        ):
            issues.append(_issue(OUTPUT_INVALID, "scope.max_parallelism must be a positive integer"))
        capabilities = set(scope.get("allowed_capabilities") or [])
        if not capabilities <= KNOWN_CAPABILITIES:
            issues.append(_issue(SCOPE_EXCEEDED, "task packet declares an unknown capability"))
        estimated_cost = scope.get("estimated_cost_usd")
        maximum_cost = scope.get("max_cost_usd")
        if (
            not isinstance(estimated_cost, (int, float))
            or isinstance(estimated_cost, bool)
            or not isfinite(float(estimated_cost))
            or estimated_cost < 0
        ):
            issues.append(_issue(OUTPUT_INVALID, "scope.estimated_cost_usd must be non-negative"))
        if (
            not isinstance(maximum_cost, (int, float))
            or isinstance(maximum_cost, bool)
            or not isfinite(float(maximum_cost))
            or maximum_cost < 0
        ):
            issues.append(_issue(OUTPUT_INVALID, "scope.max_cost_usd must be non-negative"))
        elif isinstance(estimated_cost, (int, float)) and not isinstance(estimated_cost, bool) and estimated_cost > maximum_cost:
            issues.append(_issue(SCOPE_EXCEEDED, "scope cost estimate exceeds its maximum"))
        allowed = set(scope.get("allowed_actions") or [])
        declared = set(manifest.get("authority", {}).get("allowed_actions") or [])
        required_forbidden = set(manifest.get("authority", {}).get("forbidden_actions") or [])
        if not allowed <= declared:
            issues.append(_issue(SCOPE_EXCEEDED, "task packet requests an action outside role authority"))
        if not required_forbidden <= set(scope.get("forbidden_actions") or []):
            issues.append(_issue(SCOPE_EXCEEDED, "task packet weakens role forbidden actions"))
    boundary = packet.get("evidence_boundary")
    if not isinstance(boundary, dict):
        issues.append(_issue(EVIDENCE_MISSING, "evidence boundary must be an object"))
    else:
        references = (boundary.get("record_ids") or []) + (boundary.get("result_ids") or []) + (boundary.get("artifact_revisions") or [])
        if manifest.get("evidence", {}).get("requires_source_fetch") and not references:
            issues.append(_issue(EVIDENCE_MISSING, "role requires a non-empty evidence boundary"))
    authority = packet.get("authority")
    if not isinstance(authority, dict):
        issues.append(_issue(APPROVAL_MISSING, "authority must be an object"))
    else:
        try:
            computed_scope_hash = task_scope_hash(packet)
        except (TypeError, ValueError):
            computed_scope_hash = None
        if authority.get("scope_hash") != computed_scope_hash:
            issues.append(_issue(SCOPE_EXCEEDED, "scope hash mismatch"))
        if set((scope or {}).get("allowed_actions") or []) & WRITE_ACTIONS and not authority.get("approval_refs"):
            issues.append(_issue(APPROVAL_MISSING, "write action requires approval reference"))
        for key in ("plan_refs", "user_opt_ins"):
            if key not in authority or not _nonempty_strings(authority.get(key)):
                issues.append(_issue(OUTPUT_INVALID, f"authority.{key} must be a string array"))
        if HOST_VISUALIZATION in set((scope or {}).get("allowed_capabilities") or []) and HOST_VISUALIZATION not in set(authority.get("user_opt_ins") or []):
            issues.append(_issue(APPROVAL_MISSING, "host visualization requires explicit user opt-in"))
    try:
        resolved_failure_policy = resolve_failure_policy(task=packet, environ={})
        if any(resolved_failure_policy[field] != packet.get("failure_policy", {}).get(field) for field in ("stop", "record", "detail")):
            issues.append(_issue(OUTPUT_INVALID, "failure_policy must contain a complete resolved snapshot"))
    except (AttributeError, ValueError) as exc:
        issues.append(_issue(OUTPUT_INVALID, str(exc)))
    expiration = packet.get("expires_at")
    if expiration is not None:
        try:
            expires = datetime.fromisoformat(str(expiration).replace("Z", "+00:00"))
            reference = now or datetime.now(timezone.utc)
            if expires.tzinfo is None or expires <= reference:
                issues.append(_issue(APPROVAL_MISSING, "task packet is expired"))
        except ValueError:
            issues.append(_issue(OUTPUT_INVALID, "invalid expires_at timestamp"))
    return issues


def validate_decision(decision: dict[str, Any], packet: dict[str, Any], registry: dict[str, dict[str, Any]] | None = None) -> list[dict[str, str]]:
    registry = registry or load_registry()
    issues: list[dict[str, str]] = []
    required = {"schema_version", "run_id", "workflow_id", "agent_id", "role_manifest_hash", "task_packet_hash", "status", "summary", "classification", "findings", "evidence", "commands", "decisions", "recommended_actions", "authority_used", "limitations", "attribution", "started_at", "completed_at", "output_hash"}
    missing = required - set(decision) if isinstance(decision, dict) else required
    if missing:
        return [_issue(OUTPUT_INVALID, f"decision is missing: {', '.join(sorted(missing))}")]
    if decision.get("schema_version") != "research-agent-decision/1.0":
        issues.append(_issue(OUTPUT_INVALID, "unsupported decision schema"))
    if decision.get("agent_id") != packet.get("agent_id") or decision.get("run_id") != packet.get("run_id") or decision.get("workflow_id") != packet.get("workflow_id"):
        issues.append(_issue(OUTPUT_INVALID, "decision identity does not match task packet"))
    decision_agent_id = decision.get("agent_id")
    manifest = registry.get(decision_agent_id) if isinstance(decision_agent_id, str) else None
    if manifest is None:
        issues.append(_issue(REGISTRY_UNKNOWN_AGENT, "decision has an unknown agent"))
    elif decision.get("role_manifest_hash") != manifest_hash(manifest):
        issues.append(_issue(OUTPUT_INVALID, "decision role manifest hash mismatch"))
    try:
        computed_packet_hash = artifact_hash(packet)
    except (TypeError, ValueError):
        computed_packet_hash = None
    if decision.get("task_packet_hash") != computed_packet_hash:
        issues.append(_issue(OUTPUT_INVALID, "decision task packet hash mismatch"))
    try:
        computed_output_hash = hash_without(decision, "output_hash")
    except (TypeError, ValueError):
        computed_output_hash = None
    if decision.get("output_hash") != computed_output_hash:
        issues.append(_issue(OUTPUT_INVALID, "decision output hash mismatch"))
    if decision.get("status") not in {"pass", "warn", "fail", "inconclusive", "blocked"}:
        issues.append(_issue(OUTPUT_INVALID, "invalid decision status"))
    if not isinstance(decision.get("summary"), str) or not decision["summary"].strip():
        issues.append(_issue(OUTPUT_INVALID, "decision summary must be non-empty"))
    if not isinstance(decision.get("classification"), dict):
        issues.append(_issue(OUTPUT_INVALID, "classification must be an object"))
    for field in (
        "evidence", "commands", "decisions", "recommended_actions",
        "authority_used", "limitations",
    ):
        if not isinstance(decision.get(field), list):
            issues.append(_issue(OUTPUT_INVALID, f"{field} must be an array"))
    if isinstance(decision.get("commands"), list) and decision["commands"]:
        issues.append(_issue(SCOPE_EXCEEDED, "provider-backed governance agents cannot return executed commands"))
    attribution = decision.get("attribution")
    allowed_attribution = {
        "requester", "proposer", "executor", "reviewer", "provider_reported_model",
    }
    if not isinstance(attribution, dict):
        issues.append(_issue(OUTPUT_INVALID, "attribution must be an object"))
    else:
        if not set(attribution) <= allowed_attribution:
            issues.append(_issue(OUTPUT_INVALID, "attribution contains unsupported fields"))
        for field in ("requester", "proposer", "executor", "reviewer"):
            if not isinstance(attribution.get(field), str):
                issues.append(_issue(OUTPUT_INVALID, f"attribution.{field} must be a string"))
        reported_model = attribution.get("provider_reported_model")
        if reported_model is not None and (
            not isinstance(reported_model, str) or not reported_model.strip()
        ):
            issues.append(_issue(OUTPUT_INVALID, "attribution.provider_reported_model must be non-empty"))
    if not isinstance(decision.get("findings"), list):
        issues.append(_issue(OUTPUT_INVALID, "findings must be an array"))
    else:
        for finding in decision["findings"]:
            if not isinstance(finding, dict) or not finding.get("evidence_refs"):
                issues.append(_issue(EVIDENCE_MISSING, "each material finding requires evidence references"))
    allowed = set((packet.get("scope") or {}).get("allowed_actions") or [])
    if not set(decision.get("authority_used") or []) <= allowed:
        issues.append(_issue(SCOPE_EXCEEDED, "decision used authority outside task packet"))
    if decision.get("agent_id") in CRITICAL and set(decision.get("authority_used") or []) & WRITE_ACTIONS:
        issues.append(_issue(SCOPE_EXCEEDED, "critical reviewer cannot use write authority"))
    if decision.get("agent_id") == "scope_and_cost_governor":
        issues.extend(validate_scope_governor_decision(decision))
    return issues
