"""Deterministic workflow states and mode-specific transition tables."""

from __future__ import annotations

from typing import Any


MODE_STAGES = {
    "lightweight": (
        "draft", "scope_cost_review", "awaiting_plan_approval", "approved", "retrieval_review",
        "authorized_work", "result_available", "analysis_review", "memory_sync", "completed",
    ),
    "benchmark": (
        "draft", "scope_cost_review", "awaiting_plan_approval", "approved", "retrieval_review",
        "benchmark_precheck", "authorized_work", "result_available", "analysis_review",
        "paper_evidence_review", "correction_cycle", "memory_sync", "completed",
    ),
    "final_review": (
        "draft", "scope_cost_review", "awaiting_plan_approval", "approved", "retrieval_review",
        "benchmark_precheck", "authorized_work", "result_available", "analysis_review",
        "paper_evidence_review", "correction_cycle", "memory_sync", "critical_review",
        "user_decision", "completed",
    ),
}
EXCEPTION_STATES = frozenset({"blocked", "inconclusive", "stopped", "approval_expired", "evidence_missing", "integrity_failed", "output_invalid", "ledger_corrupt"})


def initial_state(mode: str) -> dict[str, Any]:
    if mode not in MODE_STAGES:
        raise ValueError("unknown workflow mode")
    return {"schema_version": "workflow-state/2.0", "mode": mode, "stage": "draft", "history": []}


def transition(state: dict[str, Any], target: str, reason: str) -> dict[str, Any]:
    """Return a new state only for legal forward or explicit exception transitions."""

    mode = state.get("mode")
    current = state.get("stage")
    if not isinstance(mode, str) or not isinstance(current, str):
        raise ValueError("workflow state has no valid mode/stage")
    stages = MODE_STAGES.get(mode, ())
    if target in EXCEPTION_STATES:
        allowed = True
    elif current in EXCEPTION_STATES:
        allowed = False
    else:
        try:
            allowed = stages.index(target) == stages.index(current) + 1
        except ValueError:
            allowed = False
    if not allowed:
        raise ValueError(f"illegal workflow transition: {current} -> {target}")
    history = list(state.get("history") or [])
    history.append({"from": current, "to": target, "reason": reason})
    return {**state, "stage": target, "history": history}
