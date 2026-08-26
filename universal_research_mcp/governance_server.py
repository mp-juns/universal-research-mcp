"""Read-only MCP surface for URAG contracts; no worker or write capability."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from universal_research_mcp.session_scope import SESSION_SCOPE_INSTRUCTIONS

from universal_research_mcp.governance.escalation import evaluate_gate
from universal_research_mcp.governance.failure_policy import build_failure_record, resolve_failure_policy
from universal_research_mcp.governance.prompts import load_prompt_pack, prompt_registry_report
from universal_research_mcp.governance.registry import GOVERNANCE_VERSION, load_registry, manifest_hash, registry_report
from universal_research_mcp.governance.scope_policy import assess_plan_necessity, operation_gate
from universal_research_mcp.governance.validation import validate_decision, validate_task_packet
from universal_research_mcp.integrations.codex.adapter import (
    build_critical_review_batch,
    build_dispatch_request,
    build_scope_governor_receipt,
    capture_decision,
)


mcp = FastMCP("Universal Research Governance", instructions=SESSION_SCOPE_INSTRUCTIONS + "\n\n" + (
    "Validate local research-governance contracts. This server is read-only: it does not "
    "dispatch agents, call models, write research records, run commands, or rebuild indexes."
))


@mcp.tool()
def governance_get_capabilities() -> dict[str, Any]:
    report = registry_report()
    return {
        "version": GOVERNANCE_VERSION,
        "modes": ["lightweight", "benchmark", "final_review"],
        "read_only": True,
        "prompt_registry": prompt_registry_report(),
        **report,
    }


@mcp.tool()
def governance_get_role_manifest(agent_id: str) -> dict[str, Any]:
    manifest = load_registry().get(agent_id)
    if manifest is None:
        raise ValueError("unknown governance agent")
    return {"manifest": manifest, "manifest_hash": manifest_hash(manifest)}


@mcp.tool()
def governance_get_role_prompt_contract(agent_id: str) -> dict[str, Any]:
    """Return the internal versioned prompt contract for one registered role."""

    return load_prompt_pack(agent_id)


@mcp.tool()
def governance_validate_task_packet(packet: dict[str, Any]) -> dict[str, Any]:
    issues = validate_task_packet(packet)
    return {"valid": not issues, "issues": issues}


@mcp.tool()
def governance_validate_decision(decision: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    issues = validate_decision(decision, packet)
    return {"valid": not issues, "issues": issues}


@mcp.tool()
def governance_evaluate_gate(decisions: list[dict[str, Any]], claim_type: str = "publication") -> dict[str, Any]:
    return evaluate_gate(decisions, claim_type)


@mcp.tool()
def governance_prepare_codex_dispatch(
    packet: dict[str, Any], governor_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a non-executing, host-owned Codex dispatch manifest."""

    return build_dispatch_request(packet, governor_receipt)


@mcp.tool()
def governance_prepare_scope_governor_receipt(
    governor_packet: dict[str, Any],
    governor_decision: dict[str, Any],
    governed_packets: list[dict[str, Any]],
) -> dict[str, Any]:
    captured = capture_decision(governor_packet, governor_decision)
    return build_scope_governor_receipt(governor_packet, captured, governed_packets)


@mcp.tool()
def governance_prepare_codex_critical_batch(
    packets: list[dict[str, Any]], governor_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build isolated same-snapshot dispatch requests for the critical reviewers."""

    return build_critical_review_batch(packets, governor_receipt)


@mcp.tool()
def governance_capture_codex_decision(packet: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Validate a returned host decision without writing it to the ledger."""

    return capture_decision(packet, decision)


@mcp.tool()
def governance_assess_plan(operation: dict[str, Any]) -> dict[str, Any]:
    return assess_plan_necessity(operation)


@mcp.tool()
def governance_evaluate_operation(operation: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Evaluate declarative preflight; never authorize or execute a host tool call."""

    return operation_gate(operation, packet)


@mcp.tool()
def governance_resolve_failure_policy(task: dict[str, Any] | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    return resolve_failure_policy(task=task, profile=profile)


@mcp.tool()
def governance_prepare_failure_record(failure: dict[str, Any], task: dict[str, Any] | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_failure_record(failure, resolve_failure_policy(task=task, profile=profile))


def main() -> int:
    mcp.run()
    return 0
