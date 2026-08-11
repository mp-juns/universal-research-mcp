"""Deterministic policy checks for the fixed research-agent governance roster.

This module schedules nothing and calls no model.  It validates the task and
decision artifacts an execution adapter must exchange, so the policy boundary
is testable independently from any chosen worker backend.
"""

from __future__ import annotations

from typing import Any, Iterable

GOVERNANCE_VERSION = "agent-governance/2.0"
SCOPE_AND_COST_GOVERNOR = "scope_and_cost_governor"


OPERATIONAL_AGENT_IDS = frozenset({
    "retrieval_governor",
    "benchmark_control_auditor",
    "analysis_objectivity_auditor",
    "paper_evidence_evaluator",
    "correction_executor",
    "research_memory_maintainer",
    SCOPE_AND_COST_GOVERNOR,
})
CRITICAL_AGENT_IDS = frozenset({
    "cold_adversarial_reviewer",
    "substance_reviewer",
    "user_alignment_reviewer",
    "reproducibility_reviewer",
})
AGENT_IDS = OPERATIONAL_AGENT_IDS | CRITICAL_AGENT_IDS

MODE_AGENT_IDS = {
    "lightweight": frozenset({
        "retrieval_governor",
        "analysis_objectivity_auditor",
        "research_memory_maintainer",
        SCOPE_AND_COST_GOVERNOR,
    }),
    "benchmark": OPERATIONAL_AGENT_IDS,
    "final_review": AGENT_IDS,
}

ROLE_ACTIONS = {
    "retrieval_governor": frozenset({"search", "fetch", "inspect", "review"}),
    "benchmark_control_auditor": frozenset({"inspect", "review"}),
    "analysis_objectivity_auditor": frozenset({"inspect", "review"}),
    "paper_evidence_evaluator": frozenset({"inspect", "review"}),
    "correction_executor": frozenset({"inspect", "edit", "repair"}),
    "research_memory_maintainer": frozenset({"inspect", "repair", "index"}),
    SCOPE_AND_COST_GOVERNOR: frozenset({"assess_plan", "estimate", "validate_scope", "request_user_decision"}),
    "cold_adversarial_reviewer": frozenset({"inspect", "review"}),
    "substance_reviewer": frozenset({"inspect", "review"}),
    "user_alignment_reviewer": frozenset({"inspect", "review"}),
    "reproducibility_reviewer": frozenset({"inspect", "review"}),
}

TASK_FIELDS = {
    "run_id", "agent_id", "requester", "purpose", "scope",
    "evidence_boundary", "success_criteria", "stop_conditions",
}
DECISION_FIELDS = {
    "schema_version", "run_id", "agent_id", "status", "summary",
    "findings", "evidence", "commands", "decisions", "recommended_actions",
    "authority_used", "limitations", "attribution",
}


def _issues_for_string_list(value: Any, path: str, issues: list[str]) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        issues.append(f"{path} must be an array of non-empty strings")


def active_agents(mode: str, activation_gate: str | None = None) -> frozenset[str]:
    """Return the fixed, approved roster for one workflow mode and gate."""

    agents = MODE_AGENT_IDS.get(mode, frozenset())
    if mode != "final_review":
        return agents
    if activation_gate not in {"main_result", "final_submission"}:
        return frozenset(agent for agent in agents if agent not in CRITICAL_AGENT_IDS)
    return agents


def validate_task_packet(packet: dict[str, Any], mode: str, activation_gate: str | None = None) -> list[str]:
    """Fail closed on missing authority, inactive roles, or malformed task scope."""

    issues: list[str] = []
    if not isinstance(packet, dict):
        return ["task packet must be an object"]
    missing = sorted(TASK_FIELDS - set(packet))
    if missing:
        issues.append(f"task packet is missing required fields: {', '.join(missing)}")
    if not isinstance(packet.get("run_id"), str) or not packet.get("run_id", "").strip():
        issues.append("/run_id must be a non-empty string")
    agent_id = packet.get("agent_id")
    if agent_id not in AGENT_IDS:
        issues.append("/agent_id must identify one of the fixed eleven roles")
    elif agent_id not in active_agents(mode, activation_gate):
        issues.append("/agent_id is not active for this mode and activation gate")
    if packet.get("requester") not in {"user", "main_agent", "workflow"}:
        issues.append("/requester must be user, main_agent, or workflow")
    if not isinstance(packet.get("purpose"), str) or not packet.get("purpose", "").strip():
        issues.append("/purpose must be a non-empty string")

    scope = packet.get("scope")
    if not isinstance(scope, dict):
        issues.append("/scope must be an object")
    else:
        for field in ("allowed_paths", "allowed_sources", "allowed_actions", "forbidden_actions"):
            _issues_for_string_list(scope.get(field), f"/scope/{field}", issues)
        if agent_id in ROLE_ACTIONS and isinstance(scope.get("allowed_actions"), list):
            unpermitted = sorted(set(scope["allowed_actions"]) - ROLE_ACTIONS[agent_id])
            if unpermitted:
                issues.append(f"/scope/allowed_actions exceeds {agent_id} authority: {', '.join(unpermitted)}")

    boundary = packet.get("evidence_boundary")
    if not isinstance(boundary, dict):
        issues.append("/evidence_boundary must be an object")
    else:
        for field in ("result_ids", "dataset_hashes", "model_hashes", "commit_ids"):
            _issues_for_string_list(boundary.get(field), f"/evidence_boundary/{field}", issues)
    _issues_for_string_list(packet.get("success_criteria"), "/success_criteria", issues)
    _issues_for_string_list(packet.get("stop_conditions"), "/stop_conditions", issues)
    return issues


def validate_decision_record(record: dict[str, Any], packet: dict[str, Any]) -> list[str]:
    """Validate a machine-auditable decision record against its task packet."""

    issues: list[str] = []
    if not isinstance(record, dict):
        return ["decision record must be an object"]
    missing = sorted(DECISION_FIELDS - set(record))
    if missing:
        issues.append(f"decision record is missing required fields: {', '.join(missing)}")
    if record.get("schema_version") != "research-agent-decision-v1":
        issues.append("/schema_version must be research-agent-decision-v1")
    if record.get("run_id") != packet.get("run_id"):
        issues.append("/run_id must match the task packet")
    if record.get("agent_id") != packet.get("agent_id"):
        issues.append("/agent_id must match the task packet")
    if record.get("status") not in {"pass", "warn", "fail", "inconclusive", "blocked"}:
        issues.append("/status must be pass, warn, fail, inconclusive, or blocked")
    if not isinstance(record.get("summary"), str) or not record.get("summary", "").strip():
        issues.append("/summary must be a non-empty string")
    for field in ("evidence", "commands", "decisions", "recommended_actions", "authority_used", "limitations"):
        if not isinstance(record.get(field), list):
            issues.append(f"/{field} must be an array")
    attribution = record.get("attribution")
    if not isinstance(attribution, dict) or set(attribution) != {"requester", "proposer", "executor", "reviewer"}:
        issues.append("/attribution must contain requester, proposer, executor, and reviewer")

    findings = record.get("findings")
    if not isinstance(findings, list):
        issues.append("/findings must be an array")
    else:
        for number, finding in enumerate(findings):
            issues.extend(_validate_finding(finding, number))

    allowed_actions = set((packet.get("scope") or {}).get("allowed_actions") or [])
    authority_used = record.get("authority_used")
    if isinstance(authority_used, list):
        unapproved = sorted(set(authority_used) - allowed_actions)
        if unapproved:
            issues.append(f"/authority_used exceeds task scope: {', '.join(unapproved)}")
    return issues


def _validate_finding(finding: Any, number: int) -> list[str]:
    prefix = f"/findings/{number}"
    if not isinstance(finding, dict):
        return [f"{prefix} must be an object"]
    issues: list[str] = []
    for field in ("finding_id", "claim", "impact", "recommended_fix", "confidence"):
        if not isinstance(finding.get(field), str) or not finding.get(field, "").strip():
            issues.append(f"{prefix}/{field} must be a non-empty string")
    if finding.get("severity") not in {"critical", "high", "medium", "low", "note"}:
        issues.append(f"{prefix}/severity is invalid")
    if finding.get("confidence") not in {"high", "medium", "low"}:
        issues.append(f"{prefix}/confidence is invalid")
    refs = finding.get("evidence_refs")
    if not isinstance(refs, list):
        issues.append(f"{prefix}/evidence_refs must be an array")
    else:
        for ref_number, reference in enumerate(refs):
            ref_prefix = f"{prefix}/evidence_refs/{ref_number}"
            if not isinstance(reference, dict):
                issues.append(f"{ref_prefix} must be an object")
                continue
            if not isinstance(reference.get("path"), str) or not reference.get("path", "").strip():
                issues.append(f"{ref_prefix}/path must be a non-empty string")
            if not isinstance(reference.get("line_start"), int) or reference["line_start"] < 0:
                issues.append(f"{ref_prefix}/line_start must be a non-negative integer")
            if not isinstance(reference.get("line_end"), int) or reference["line_end"] < reference.get("line_start", 0):
                issues.append(f"{ref_prefix}/line_end must be no smaller than line_start")
            if "hash" in reference and not isinstance(reference["hash"], str):
                issues.append(f"{ref_prefix}/hash must be a string when supplied")
    return issues


def claim_gate(decisions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded publication-claim gate; never resolve a finding itself."""

    blockers: list[dict[str, Any]] = []
    for decision in decisions:
        for finding in decision.get("findings", []) if isinstance(decision, dict) else []:
            if isinstance(finding, dict) and finding.get("severity") in {"critical", "high"}:
                blockers.append({
                    "agent_id": decision.get("agent_id"),
                    "finding_id": finding.get("finding_id"),
                    "severity": finding.get("severity"),
                    "claim": finding.get("claim"),
                })
        if isinstance(decision, dict) and decision.get("status") in {"blocked", "inconclusive"}:
            blockers.append({
                "agent_id": decision.get("agent_id"),
                "finding_id": None,
                "severity": "inconclusive",
                "claim": "Reviewer did not establish a positive conclusion.",
            })
    return {
        "claim_eligibility": "blocked" if blockers else "eligible",
        "blockers": blockers,
        "requires_user_decision": bool(blockers),
    }


def user_chat_report(
    *,
    status: str,
    outcome: str,
    risks: list[str] | None = None,
    blockers: list[str] | None = None,
    required_choices: list[str] | None = None,
    metrics: dict[str, str | int | float] | None = None,
    artifact_refs: list[str] | None = None,
    executed_state: str,
    detail_requested: bool = False,
) -> dict[str, Any]:
    """Create the summary-only chat envelope required by the central manager."""

    if executed_state not in {"executed", "reviewed", "indexed", "blocked", "proposed"}:
        raise ValueError("invalid executed state")
    return {
        "status": status,
        "outcome": outcome,
        "risks": risks or [],
        "blockers": blockers or [],
        "required_choices": required_choices or [],
        "metrics": metrics or {},
        "artifact_refs": artifact_refs or [],
        "executed_state": executed_state,
        "chat_disclosure": "user_requested_detail" if detail_requested else "summary_only",
    }
