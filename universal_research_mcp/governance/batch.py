"""Pure, non-executing validation for bounded parallel task packets."""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

from universal_research_mcp.governance.registry import CRITICAL, SCOPE_AND_COST_GOVERNOR
from universal_research_mcp.governance.validation import validate_task_packet
from universal_research_mcp.integrations.codex.adapter import validate_critical_review_batch


def preflight_parallel_batch(
    packets: list[dict[str, Any]],
    *,
    max_workers: int,
    aggregate_cost_ceiling_usd: float,
    declared_costs_usd: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Validate a complete batch without importing an execution harness."""

    issues: list[dict[str, str]] = []
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
        issues.append({"code": "GOV-COST-001", "message": "max_workers must be positive"})
    if (
        not isinstance(aggregate_cost_ceiling_usd, (int, float))
        or isinstance(aggregate_cost_ceiling_usd, bool)
        or not isfinite(float(aggregate_cost_ceiling_usd))
        or aggregate_cost_ceiling_usd < 0
    ):
        issues.append({"code": "GOV-COST-001", "message": "aggregate cost ceiling is invalid"})
        return _report(packets, max_workers, aggregate_cost_ceiling_usd, issues)
    if not packets:
        issues.append({"code": "GOV-PLAN-001", "message": "at least one task packet is required"})
        return _report(packets, max_workers, aggregate_cost_ceiling_usd, issues)
    identities = [packet.get("agent_id") for packet in packets if isinstance(packet, dict)]
    if identities.count(SCOPE_AND_COST_GOVERNOR) != 1:
        issues.append({
            "code": "GOV-PLAN-001",
            "message": "exactly one scope_and_cost_governor packet is required",
        })
    if len(identities) != len(set(identities)):
        issues.append({"code": "GOV-REGISTRY-002", "message": "agent packets must be unique"})
    for packet in packets:
        issues.extend(validate_task_packet(packet))
    workers = [packet for packet in packets if packet.get("agent_id") != SCOPE_AND_COST_GOVERNOR]
    critical = [packet for packet in workers if packet.get("agent_id") in CRITICAL]
    if critical:
        if len(critical) != len(workers):
            issues.append({
                "code": "GOV-REGISTRY-002",
                "message": "critical and operational batches cannot be mixed",
            })
        else:
            issues.extend(validate_critical_review_batch(critical))
    if workers:
        parallel_ceiling = min(int(packet["scope"]["max_parallelism"]) for packet in workers)
        if max_workers > parallel_ceiling:
            issues.append({"code": "GOV-COST-001", "message": "max_workers exceeds task scope"})
    total_cost = 0.0
    for packet in packets:
        agent_id = str(packet.get("agent_id"))
        if declared_costs_usd is None:
            raw_cost = (packet.get("scope") or {}).get("estimated_cost_usd")
        elif agent_id not in declared_costs_usd:
            issues.append({
                "code": "GOV-COST-001",
                "message": f"explicit declared cost is missing for {agent_id}",
            })
            continue
        else:
            raw_cost = declared_costs_usd[agent_id]
        if (
            not isinstance(raw_cost, (int, float))
            or isinstance(raw_cost, bool)
            or not isfinite(float(raw_cost))
            or raw_cost < 0
        ):
            issues.append({
                "code": "GOV-COST-001",
                "message": f"missing or invalid declared cost for {agent_id}",
            })
            continue
        cost = float(raw_cost)
        if cost > float(packet["scope"]["max_cost_usd"]):
            issues.append({
                "code": "GOV-COST-001",
                "message": f"declared cost exceeds {agent_id} scope",
            })
        total_cost += cost
    if total_cost > float(aggregate_cost_ceiling_usd):
        issues.append({
            "code": "GOV-COST-001", "message": "declared aggregate cost exceeds ceiling",
        })
    return _report(packets, max_workers, aggregate_cost_ceiling_usd, issues)


def _report(
    packets: list[dict[str, Any]], max_workers: int,
    aggregate_cost_ceiling_usd: float, issues: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": "parallel-research-preflight/1.0",
        "valid": not issues,
        "issues": issues,
        "packet_count": len(packets),
        "max_workers": max_workers,
        "aggregate_cost_ceiling_usd": aggregate_cost_ceiling_usd,
        "executed": False,
    }


__all__ = ["preflight_parallel_batch"]
