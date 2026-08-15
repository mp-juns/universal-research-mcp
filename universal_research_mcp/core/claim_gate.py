"""Deterministic evidence-eligibility gate for material research claims.

Candidate retrieval is intentionally not enough for a load-bearing conclusion.
This module classifies the *consequence* of a claim, then evaluates only the
verification facts supplied by a host-owned evidence resolver.  It never
decides whether prose is scientifically true and never calls a model.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


CLAIM_TYPES = frozenset({
    "factual", "result", "comparative", "causal", "release",
    "recommendation", "creative",
})
MATERIALITIES = frozenset({"auto", "routine", "material"})
AUTO_MATERIAL_TYPES = frozenset({"result", "comparative", "causal", "release"})
TWO_SOURCE_TYPES = frozenset({"comparative", "causal", "release"})


def evidence_eligibility_plan(claim_type: str, materiality: str = "auto") -> dict[str, Any]:
    """Classify whether a claim requires source verification.

    ``auto`` deliberately uses claim consequence rather than keyword matching:
    release, causal, comparative, and reported-result claims are material;
    routine factual lookup remains a normal RAG operation.  A caller can mark a
    factual statement ``material`` when it carries a decision or publication.
    """

    if claim_type not in CLAIM_TYPES:
        raise ValueError(f"unsupported claim_type: {claim_type}")
    if materiality not in MATERIALITIES:
        raise ValueError(f"unsupported materiality: {materiality}")
    active = (
        materiality == "material"
        or (materiality == "auto" and claim_type in AUTO_MATERIAL_TYPES)
    )
    minimum = 2 if claim_type in TWO_SOURCE_TYPES else 1
    if not active:
        minimum = 0
    return {
        "claim_type": claim_type,
        "materiality": materiality,
        "active": active,
        "minimum_distinct_evidence": minimum,
        "activation_reason": (
            "caller_marked_material" if materiality == "material" else
            "material_claim_type" if active else "routine_claim"
        ),
    }


def evaluate_evidence_eligibility(
    *,
    claim_type: str,
    materiality: str,
    evidence_checks: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return an eligibility verdict from already-resolved evidence checks.

    A check is valid only when its host resolver sets ``verified`` true.  The
    resolver owns path safety, registered revision lookup, current hash
    comparison, and requested-range checks; callers cannot mark evidence valid
    merely by echoing a path or hash in model-authored JSON.
    """

    plan = evidence_eligibility_plan(claim_type, materiality)
    checks = [dict(item) for item in evidence_checks]
    if not plan["active"]:
        return {
            "schema_version": "research-evidence-eligibility/1.0",
            **plan,
            "status": "not_required",
            "evidence_eligibility": "not_required",
            "claim_eligibility": "not_required",
            "claim_verified": False,
            "semantic_support_checked": False,
            "conflict_checked": False,
            "source_truth_checked": False,
            "evidence": [],
            "blockers": [],
            "requires_user_decision": False,
        }

    evidence: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    distinct: set[str] = set()
    for index, check in enumerate(checks, start=1):
        event_id = check.get("event_id")
        path = check.get("path")
        verified = check.get("verified") is True
        if isinstance(event_id, str) and event_id:
            distinct.add(event_id)
        record = {
            "event_id": event_id,
            "path": path,
            "start_line": check.get("start_line"),
            "end_line": check.get("end_line"),
            "expected_sha256": check.get("expected_sha256"),
            "current_sha256": check.get("current_sha256"),
            "integrity_status": check.get("integrity_status"),
            "verified": verified,
        }
        evidence.append(record)
        if not verified:
            blockers.append({
                "code": "EVIDENCE-INTEGRITY-INVALID",
                "reason": str(check.get("reason") or "evidence could not be verified"),
                "evidence_index": str(index),
            })
    if len(distinct) < plan["minimum_distinct_evidence"]:
        blockers.append({
            "code": "EVIDENCE-ELIGIBILITY-INSUFFICIENT",
            "reason": (
                f"{claim_type} claim requires {plan['minimum_distinct_evidence']} "
                "distinct verified evidence records"
            ),
            "evidence_index": "",
        })
    blocked = bool(blockers)
    return {
        "schema_version": "research-evidence-eligibility/1.0",
        **plan,
        "status": "blocked" if blocked else "eligible",
        "evidence_eligibility": "blocked" if blocked else "eligible",
        "claim_eligibility": "blocked" if blocked else "eligible",
        "claim_verified": False,
        "semantic_support_checked": False,
        "conflict_checked": False,
        "source_truth_checked": False,
        "evidence": evidence,
        "blockers": blockers,
        "requires_user_decision": blocked,
    }


# Python compatibility aliases. They are not the canonical MCP tool contract.
claim_gate_plan = evidence_eligibility_plan
evaluate_claim_gate = evaluate_evidence_eligibility


__all__ = [
    "CLAIM_TYPES", "MATERIALITIES", "evidence_eligibility_plan",
    "evaluate_evidence_eligibility",
]
