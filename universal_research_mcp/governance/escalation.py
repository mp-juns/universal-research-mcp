"""Non-LLM aggregation for reviewer and finding escalation."""

from __future__ import annotations

from typing import Any, Iterable


def evaluate_gate(decisions: Iterable[dict[str, Any]], claim_type: str = "publication") -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    verdicts: dict[str, str] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        agent_id = str(decision.get("agent_id", ""))
        classification = decision.get("classification") or {}
        verdict = classification.get("reviewer_verdict")
        if isinstance(verdict, str):
            verdicts[agent_id] = verdict
        if decision.get("status") in {"blocked", "inconclusive"}:
            blockers.append({"code": "GOV-GATE-001", "agent_id": agent_id, "reason": "review is blocked or inconclusive"})
        for finding in decision.get("findings") or []:
            severity = finding.get("severity") if isinstance(finding, dict) else None
            if severity == "critical":
                blockers.append({"code": "GOV-GATE-001", "agent_id": agent_id, "finding_id": finding.get("finding_id"), "reason": "critical finding blocks publication-facing claims"})
            elif severity == "high" and claim_type in {"publication", "comparative", "causal"}:
                blockers.append({"code": "GOV-GATE-002", "agent_id": agent_id, "finding_id": finding.get("finding_id"), "reason": "high finding blocks this claim type"})
    distinct = set(verdicts.values())
    if len(distinct) > 1 and {"reject_claim", "no_material_objection_found"} <= distinct:
        blockers.append({"code": "GOV-CONFLICT-001", "agent_id": "aggregation", "reason": "critical reviewer verdicts conflict"})
    return {"claim_type": claim_type, "eligible": not blockers, "blockers": blockers, "requires_user_decision": bool(blockers), "reviewer_verdicts": verdicts}
