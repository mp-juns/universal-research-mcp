"""Translate validated URAG packets into safe, host-executed Codex requests.

This adapter is deliberately not a Codex-agent launcher. A plugin/MCP server
cannot assume access to the host's private multi-agent scheduler. The host uses
the returned request to dispatch under its own entitlement and permissions.
"""

from __future__ import annotations

from typing import Any

from governance.hashing import artifact_hash, canonical_json, hash_without
from governance.prompts import load_prompt_pack, render_prompt_pack
from governance.registry import CRITICAL, SCOPE_AND_COST_GOVERNOR, load_registry, manifest_hash
from governance.validation import (
    validate_decision,
    validate_scope_governor_decision,
    validate_task_packet,
)


def _render_dispatch(packet: dict[str, Any]) -> dict[str, Any]:
    """Render a packet after its caller has completed all required gates."""

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

    issues = validate_task_packet(packet)
    if issues:
        return {"dispatchable": False, "estimate_only": True, "issues": issues}
    return {**_render_dispatch(packet), "dispatchable": False, "estimate_only": True}


def build_dispatch_request(
    packet: dict[str, Any],
    governor_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render one validated and scope-governed non-executing dispatch request."""

    issues = validate_task_packet(packet)
    issues.extend(validate_scope_governor_receipt(packet, governor_receipt))
    if issues:
        return {"dispatchable": False, "issues": issues}
    request = _render_dispatch(packet)
    request["scope_governor_receipt_hash"] = (
        None if governor_receipt is None else governor_receipt.get("receipt_hash")
    )
    return request


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
    return {
        "schema_version": "urag-codex-critical-batch/1.0",
        "dispatchable": True,
        "workflow_id": workflow_ids.pop(),
        "evidence_snapshot_hash": snapshots.pop(),
        "dispatch_policy": "parallel_if_host_supports_otherwise_sequential_isolated",
        "requests": requests,
    }


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


def serialize_dispatch_manifest(dispatch: dict[str, Any]) -> str:
    """Serialize an already validated non-executing dispatch request deterministically."""

    if not isinstance(dispatch, dict) or not dispatch.get("dispatchable"):
        raise ValueError("only a dispatchable request may be exported")
    return canonical_json(dispatch)
