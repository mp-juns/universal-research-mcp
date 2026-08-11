"""Translate validated URAG packets into safe, host-executed Codex requests.

This adapter is deliberately not a Codex-agent launcher. A plugin/MCP server
cannot assume access to the host's private multi-agent scheduler. The host uses
the returned request to dispatch under its own entitlement and permissions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from universal_research_mcp.governance.hashing import artifact_hash, canonical_json, hash_without
from universal_research_mcp.governance.prompts import load_prompt_pack, render_prompt_pack
from universal_research_mcp.governance.registry import CRITICAL, SCOPE_AND_COST_GOVERNOR, load_registry, manifest_hash
from universal_research_mcp.governance.validation import (
    validate_decision,
    validate_scope_governor_decision,
    validate_task_packet,
)


_DISPATCH_FIELDS = frozenset({
    "schema_version",
    "dispatchable",
    "host",
    "run_id",
    "workflow_id",
    "agent_id",
    "task_packet_hash",
    "role_manifest_hash",
    "role_prompt_hash",
    "role_prompt",
    "execution",
    "role_instructions",
    "scope_governor_receipt_hash",
    "dispatch_hash",
})
_ROLE_INSTRUCTION_FIELDS = frozenset({
    "agent_id",
    "purpose",
    "allowed_actions",
    "forbidden_actions",
    "evidence_boundary",
    "success_criteria",
    "stop_conditions",
    "evidence_policy",
    "prompt_pack_hash",
    "scope_hash",
})
_CRITICAL_BATCH_FIELDS = frozenset({
    "schema_version",
    "dispatchable",
    "workflow_id",
    "evidence_snapshot_hash",
    "dispatch_policy",
    "requests",
    "batch_hash",
})


def _issue(message: str) -> dict[str, str]:
    return {"code": "GOV-DISPATCH-001", "message": message}


def _is_artifact_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _seal_manifest(value: dict[str, Any], field: str) -> dict[str, Any]:
    sealed = deepcopy(value)
    sealed[field] = hash_without(sealed, field)
    return sealed


def _render_dispatch(packet: dict[str, Any]) -> dict[str, Any]:
    """Render a packet after its caller has completed all required gates."""

    packet = deepcopy(packet)
    registry = load_registry()
    manifest = registry[packet["agent_id"]]
    prompt_pack = load_prompt_pack(packet["agent_id"])
    allowed_actions = packet["scope"]["allowed_actions"]
    role_instructions = {
        "agent_id": packet["agent_id"],
        "purpose": packet["purpose"],
        "allowed_actions": allowed_actions,
        "forbidden_actions": packet["scope"]["forbidden_actions"],
        "evidence_boundary": packet["evidence_boundary"],
        "success_criteria": packet["success_criteria"],
        "stop_conditions": packet["stop_conditions"],
        "evidence_policy": manifest["evidence"],
        "prompt_pack_hash": prompt_pack["prompt_pack_hash"],
        "scope_hash": packet["authority"]["scope_hash"],
    }
    critical = packet["agent_id"] in CRITICAL
    return {
        "schema_version": "urag-codex-dispatch/2.0",
        "dispatchable": True,
        "host": "codex",
        "run_id": packet["run_id"],
        "workflow_id": packet["workflow_id"],
        "agent_id": packet["agent_id"],
        "task_packet_hash": artifact_hash(packet),
        "role_manifest_hash": manifest_hash(manifest),
        "role_prompt_hash": prompt_pack["prompt_pack_hash"],
        "role_prompt": render_prompt_pack(prompt_pack),
        "execution": {
            "host_dispatch_required": True,
            "parallel_eligible": critical,
            "isolated_context": critical,
            "model_selection": "host_owned",
            "network": "not_granted_by_adapter",
            "write_execution": "not_granted_by_adapter",
        },
        "role_instructions": role_instructions,
    }


def build_scope_governor_receipt(
    governor_packet: dict[str, Any],
    captured_governor_decision: dict[str, Any],
    governed_packets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind one validated passing governor decision to exact task/scope hashes."""

    governor_packet = deepcopy(governor_packet)
    captured_governor_decision = deepcopy(captured_governor_decision)
    governed_packets = deepcopy(governed_packets)
    issues = validate_task_packet(governor_packet)
    if governor_packet.get("agent_id") != SCOPE_AND_COST_GOVERNOR:
        issues.append({
            "code": "GOV-PLAN-001",
            "message": "scope receipt requires the registered scope_and_cost_governor",
        })
    decision = captured_governor_decision.get("decision") or {}
    issues.extend(validate_decision(decision, governor_packet))
    if not captured_governor_decision.get("accepted") or decision.get("status") != "pass":
        issues.append({
            "code": "GOV-PLAN-001",
            "message": "scope receipt requires one validated passing governor decision",
        })
    else:
        if captured_governor_decision.get("decision_hash") != artifact_hash(decision):
            issues.append({
                "code": "GOV-PLAN-001",
                "message": "scope governor captured decision hash mismatch",
            })
        issues.extend(validate_scope_governor_decision(decision))
    governed: list[dict[str, str]] = []
    for packet in governed_packets:
        issues.extend(validate_task_packet(packet))
        if (
            packet.get("run_id") != governor_packet.get("run_id")
            or packet.get("workflow_id") != governor_packet.get("workflow_id")
        ):
            issues.append({
                "code": "GOV-PLAN-001",
                "message": "governed task identity must match the governor workflow",
            })
        if packet.get("agent_id") == SCOPE_AND_COST_GOVERNOR:
            issues.append({
                "code": "GOV-PLAN-001",
                "message": "scope governor cannot issue a receipt for itself",
            })
        governed.append({
            "agent_id": str(packet.get("agent_id")),
            "task_packet_hash": artifact_hash(packet),
            "scope_hash": str((packet.get("authority") or {}).get("scope_hash") or ""),
            "prompt_pack_hash": load_prompt_pack(str(packet.get("agent_id")))["prompt_pack_hash"],
        })
    if not governed:
        issues.append({"code": "GOV-PLAN-001", "message": "scope receipt must govern at least one task"})
    if issues:
        return {"valid": False, "issues": issues}
    receipt = {
        "schema_version": "scope-governor-receipt/1.0",
        "record_type": "scope_governor_receipt",
        "timestamp": str(decision.get("completed_at")),
        "run_id": governor_packet["run_id"],
        "workflow_id": governor_packet["workflow_id"],
        "agent_id": SCOPE_AND_COST_GOVERNOR,
        "user_visible_summary": (
            "Scope and cost assessment passed for the already approved, bound task packets."
        ),
        "internal_artifact_refs": [str(captured_governor_decision["decision_hash"])],
        "commands_or_operations": ["bind_scope_governor_receipt"],
        "decision": "scope_assessment_bound",
        "governor_task_hash": artifact_hash(governor_packet),
        "governor_decision_hash": str(captured_governor_decision["decision_hash"]),
        "governor_status": "pass",
        "governed_tasks": sorted(governed, key=lambda item: (item["agent_id"], item["task_packet_hash"])),
        "authority_basis": (
            "explicit task approval references plus a validated scope-and-cost assessment, "
            "bound by the deterministic controller"
        ),
        "chat_disclosure": {
            "mode": "summary_only",
            "reason": "default central manager disclosure policy",
        },
    }
    receipt["receipt_hash"] = hash_without(receipt, "receipt_hash")
    return {"valid": True, "issues": [], "receipt": receipt}


def validate_scope_governor_receipt(
    packet: dict[str, Any], receipt: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Require an untampered receipt covering this exact packet and scope."""

    if packet.get("agent_id") == SCOPE_AND_COST_GOVERNOR:
        return []
    if not isinstance(receipt, dict):
        return [{"code": "GOV-PLAN-001", "message": "validated scope governor receipt is required"}]
    issues: list[dict[str, str]] = []
    if receipt.get("schema_version") != "scope-governor-receipt/1.0":
        issues.append({"code": "GOV-PLAN-001", "message": "unsupported scope governor receipt"})
    if receipt.get("receipt_hash") != hash_without(receipt, "receipt_hash"):
        issues.append({"code": "GOV-PLAN-001", "message": "scope governor receipt hash mismatch"})
    if receipt.get("governor_status") != "pass":
        issues.append({"code": "GOV-PLAN-001", "message": "scope governor receipt is not passing"})
    if receipt.get("run_id") != packet.get("run_id") or receipt.get("workflow_id") != packet.get("workflow_id"):
        issues.append({"code": "GOV-PLAN-001", "message": "scope governor receipt workflow mismatch"})
    expected = {
        "agent_id": str(packet.get("agent_id")),
        "task_packet_hash": artifact_hash(packet),
        "scope_hash": str((packet.get("authority") or {}).get("scope_hash") or ""),
        "prompt_pack_hash": load_prompt_pack(str(packet.get("agent_id")))["prompt_pack_hash"],
    }
    governed = receipt.get("governed_tasks")
    if not isinstance(governed, list) or expected not in governed:
        issues.append({"code": "GOV-PLAN-001", "message": "scope governor receipt does not cover this exact task"})
    return issues


def build_dispatch_draft(packet: dict[str, Any]) -> dict[str, Any]:
    """Render a non-executable draft for deterministic cost estimation only."""

    packet = deepcopy(packet)
    issues = validate_task_packet(packet)
    if issues:
        return {"dispatchable": False, "estimate_only": True, "issues": issues}
    return _seal_manifest(
        {**_render_dispatch(packet), "dispatchable": False, "estimate_only": True},
        "dispatch_hash",
    )


def build_dispatch_request(
    packet: dict[str, Any],
    governor_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render one validated and scope-governed non-executing dispatch request."""

    packet = deepcopy(packet)
    governor_receipt = deepcopy(governor_receipt)
    issues = validate_task_packet(packet)
    issues.extend(validate_scope_governor_receipt(packet, governor_receipt))
    if issues:
        return {"dispatchable": False, "issues": issues}
    request = _render_dispatch(packet)
    request["scope_governor_receipt_hash"] = (
        None if governor_receipt is None else governor_receipt.get("receipt_hash")
    )
    return _seal_manifest(request, "dispatch_hash")


def validate_critical_review_batch(packets: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Validate the exact isolated reviewer set without rendering dispatches."""

    issues: list[dict[str, str]] = []
    expected = set(CRITICAL)
    supplied = {packet.get("agent_id") for packet in packets if isinstance(packet, dict)}
    if supplied != expected or len(packets) != len(expected):
        issues.append({"code": "GOV-REGISTRY-002", "message": "critical batch requires each of the four reviewers exactly once"})
    snapshots = {str((packet.get("evidence_boundary") or {}).get("snapshot_hash")) for packet in packets}
    workflow_ids = {packet.get("workflow_id") for packet in packets}
    if len(snapshots) != 1 or "" in snapshots or len(workflow_ids) != 1:
        issues.append({"code": "GOV-EVIDENCE-001", "message": "critical reviewers require one shared non-empty evidence snapshot"})
    return issues


def build_critical_review_batch(
    packets: list[dict[str, Any]],
    governor_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create receipt-bound, isolated same-snapshot reviewer requests."""

    packets = deepcopy(packets)
    governor_receipt = deepcopy(governor_receipt)
    issues = validate_critical_review_batch(packets)
    if issues:
        return {"dispatchable": False, "issues": issues}
    snapshots = {str((packet.get("evidence_boundary") or {}).get("snapshot_hash")) for packet in packets}
    workflow_ids = {packet.get("workflow_id") for packet in packets}
    requests = [
        build_dispatch_request(packet, governor_receipt)
        for packet in sorted(packets, key=lambda item: item["agent_id"])
    ]
    invalid = [request for request in requests if not request.get("dispatchable")]
    if invalid:
        return {"dispatchable": False, "issues": [issue for request in invalid for issue in request.get("issues", [])]}
    return _seal_manifest({
        "schema_version": "urag-codex-critical-batch/1.0",
        "dispatchable": True,
        "workflow_id": workflow_ids.pop(),
        "evidence_snapshot_hash": snapshots.pop(),
        "dispatch_policy": "parallel_if_host_supports_otherwise_sequential_isolated",
        "requests": requests,
    }, "batch_hash")


def capture_decision(packet: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Return a validation result without promoting invalid model output."""

    issues = validate_decision(decision, packet)
    if issues:
        return {
            "accepted": False,
            "artifact_kind": "invalid_agent_output",
            "raw_output_hash": artifact_hash(decision),
            "issues": issues,
            "next_action": "allow_at_most_one_structured_repair_then_mark_blocked",
        }
    return {
        "accepted": True,
        "artifact_kind": "validated_agent_decision",
        "decision_hash": artifact_hash(decision),
        "decision": decision,
    }


def _validate_single_dispatch(dispatch: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if set(dispatch) != _DISPATCH_FIELDS:
        missing = sorted(_DISPATCH_FIELDS - set(dispatch))
        unknown = sorted(str(field) for field in set(dispatch) - _DISPATCH_FIELDS)
        if missing:
            issues.append(_issue(f"dispatch fields are missing: {', '.join(missing)}"))
        if unknown:
            issues.append(_issue(f"dispatch fields are unsupported: {', '.join(unknown)}"))
    if dispatch.get("schema_version") != "urag-codex-dispatch/2.0":
        issues.append(_issue("unsupported Codex dispatch schema"))
    if dispatch.get("dispatchable") is not True or dispatch.get("host") != "codex":
        issues.append(_issue("manifest is not a dispatchable Codex request"))
    try:
        computed_dispatch_hash = hash_without(dispatch, "dispatch_hash")
    except (TypeError, ValueError):
        computed_dispatch_hash = None
        issues.append(_issue("dispatch cannot be canonically hashed"))
    if dispatch.get("dispatch_hash") != computed_dispatch_hash:
        issues.append(_issue("dispatch integrity hash mismatch"))
    for field in (
        "task_packet_hash",
        "role_manifest_hash",
        "role_prompt_hash",
        "dispatch_hash",
    ):
        if not _is_artifact_hash(dispatch.get(field)):
            issues.append(_issue(f"{field} is not an exact artifact hash"))
    receipt_hash = dispatch.get("scope_governor_receipt_hash")
    if receipt_hash is not None and not _is_artifact_hash(receipt_hash):
        issues.append(_issue("scope governor receipt hash is invalid"))

    agent_id = dispatch.get("agent_id")
    registry = load_registry()
    manifest = registry.get(agent_id) if isinstance(agent_id, str) else None
    if manifest is None:
        issues.append(_issue("dispatch references an unknown governance agent"))
        return issues
    prompt_pack = load_prompt_pack(str(agent_id))
    expected_prompt_hash = prompt_pack["prompt_pack_hash"]
    if dispatch.get("role_manifest_hash") != manifest_hash(manifest):
        issues.append(_issue("dispatch role manifest hash mismatch"))
    if dispatch.get("role_prompt_hash") != expected_prompt_hash:
        issues.append(_issue("dispatch role prompt hash mismatch"))
    if dispatch.get("role_prompt") != render_prompt_pack(prompt_pack):
        issues.append(_issue("dispatch role prompt content mismatch"))

    instructions = dispatch.get("role_instructions")
    if not isinstance(instructions, dict):
        issues.append(_issue("dispatch role instructions must be an object"))
    else:
        if set(instructions) != _ROLE_INSTRUCTION_FIELDS:
            issues.append(_issue("dispatch role instruction fields do not match the closed contract"))
        if instructions.get("agent_id") != agent_id:
            issues.append(_issue("dispatch role instruction identity mismatch"))
        if instructions.get("prompt_pack_hash") != expected_prompt_hash:
            issues.append(_issue("dispatch instruction prompt hash mismatch"))
        if not _is_artifact_hash(instructions.get("scope_hash")):
            issues.append(_issue("dispatch instruction scope hash is invalid"))
        allowed_actions = instructions.get("allowed_actions")
        if (
            not isinstance(allowed_actions, list)
            or any(not isinstance(item, str) for item in allowed_actions)
            or not set(allowed_actions) <= set(manifest["authority"]["allowed_actions"])
        ):
            issues.append(_issue("dispatch grants an action outside role authority"))
        forbidden_actions = instructions.get("forbidden_actions")
        if (
            not isinstance(forbidden_actions, list)
            or any(not isinstance(item, str) for item in forbidden_actions)
            or not set(manifest["authority"]["forbidden_actions"]) <= set(forbidden_actions)
        ):
            issues.append(_issue("dispatch weakens role forbidden actions"))
        if instructions.get("evidence_policy") != manifest["evidence"]:
            issues.append(_issue("dispatch evidence policy mismatch"))

    critical = agent_id in CRITICAL
    expected_execution = {
        "host_dispatch_required": True,
        "parallel_eligible": critical,
        "isolated_context": critical,
        "model_selection": "host_owned",
        "network": "not_granted_by_adapter",
        "write_execution": "not_granted_by_adapter",
    }
    if dispatch.get("execution") != expected_execution:
        issues.append(_issue("dispatch execution boundary was changed"))
    return issues


def validate_dispatch_manifest(
    dispatch: dict[str, Any],
    *,
    expected_manifest_hash: str,
) -> list[dict[str, str]]:
    """Revalidate a sealed manifest against a host-pinned build-time hash."""

    if not isinstance(dispatch, dict):
        return [_issue("dispatch manifest must be an object")]
    if dispatch.get("schema_version") == "urag-codex-dispatch/2.0":
        single_issues = _validate_single_dispatch(dispatch)
        if not _is_artifact_hash(expected_manifest_hash):
            single_issues.append(_issue("a valid host-pinned dispatch hash is required"))
        elif dispatch.get("dispatch_hash") != expected_manifest_hash:
            single_issues.append(_issue("dispatch does not match the host-pinned build-time hash"))
        return single_issues
    if dispatch.get("schema_version") != "urag-codex-critical-batch/1.0":
        return [_issue("unsupported dispatch manifest schema")]

    issues: list[dict[str, str]] = []
    if set(dispatch) != _CRITICAL_BATCH_FIELDS:
        issues.append(_issue("critical batch fields do not match the closed contract"))
    if dispatch.get("dispatchable") is not True:
        issues.append(_issue("critical batch is not dispatchable"))
    try:
        computed_batch_hash = hash_without(dispatch, "batch_hash")
    except (TypeError, ValueError):
        computed_batch_hash = None
        issues.append(_issue("critical batch cannot be canonically hashed"))
    if dispatch.get("batch_hash") != computed_batch_hash:
        issues.append(_issue("critical batch integrity hash mismatch"))
    if not _is_artifact_hash(expected_manifest_hash):
        issues.append(_issue("a valid host-pinned critical-batch hash is required"))
    elif dispatch.get("batch_hash") != expected_manifest_hash:
        issues.append(_issue("critical batch does not match the host-pinned build-time hash"))
    requests = dispatch.get("requests")
    if not isinstance(requests, list):
        issues.append(_issue("critical batch requests must be an array"))
        return issues
    if len(requests) != len(CRITICAL):
        issues.append(_issue("critical batch reviewer count changed"))
    for request in requests:
        if not isinstance(request, dict):
            issues.append(_issue("critical batch contains a non-object request"))
        else:
            issues.extend(_validate_single_dispatch(request))
    reviewer_ids = [
        request.get("agent_id") for request in requests if isinstance(request, dict)
    ]
    if (
        any(not isinstance(agent_id, str) for agent_id in reviewer_ids)
        or set(reviewer_ids) != set(CRITICAL)
    ):
        issues.append(_issue("critical batch reviewer set changed"))
    if any(request.get("workflow_id") != dispatch.get("workflow_id") for request in requests if isinstance(request, dict)):
        issues.append(_issue("critical batch workflow binding changed"))
    snapshots: set[str] = set()
    for request in requests:
        if not isinstance(request, dict):
            continue
        instructions = request.get("role_instructions")
        if not isinstance(instructions, dict):
            snapshots.add("")
            continue
        boundary = instructions.get("evidence_boundary")
        if not isinstance(boundary, dict):
            snapshots.add("")
            continue
        snapshots.add(str(boundary.get("snapshot_hash")))
    if snapshots != {str(dispatch.get("evidence_snapshot_hash"))}:
        issues.append(_issue("critical batch evidence snapshot binding changed"))
    return issues


def serialize_dispatch_manifest(
    dispatch: dict[str, Any],
    *,
    expected_manifest_hash: str,
) -> str:
    """Revalidate and serialize a host-pinned non-executing manifest."""

    issues = validate_dispatch_manifest(
        dispatch,
        expected_manifest_hash=expected_manifest_hash,
    )
    if issues:
        raise ValueError("dispatch manifest validation failed: " + "; ".join(
            issue["message"] for issue in issues
        ))
    return canonical_json(dispatch)
