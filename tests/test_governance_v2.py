from __future__ import annotations

import pytest

from governance.errors import COST_EXCEEDED, PLAN_REQUIRED, USER_OPT_IN_MISSING
from governance.failure_policy import build_failure_record, failure_directive, resolve_failure_policy
from governance.hashing import artifact_hash
from governance.scope_policy import (
    DATA_PLOT_GENERATION,
    HOST_VISUALIZATION,
    assess_plan_necessity,
    operation_gate,
    task_scope_hash,
    validate_operation_scope,
)
from governance.validation import validate_scope_governor_decision


def packet(*, capabilities: list[str] | None = None, opt_ins: list[str] | None = None, plan_refs: list[str] | None = None) -> dict:
    return {
        "scope": {
            "allowed_actions": ["inspect_artifact"],
            "allowed_paths": ["docs"],
            "allowed_sources": ["canonical"],
            "allowed_providers": ["openai"],
            "allowed_capabilities": capabilities or [],
            "estimated_cost_usd": 0.0, "max_cost_usd": 1.0,
        },
        "authority": {"user_opt_ins": opt_ins or [], "plan_refs": plan_refs or []},
    }


def test_plan_assessment_is_deterministic_and_declares_unknown_remote_cost() -> None:
    operation = {"action": "inspect_artifact", "provider": "openai", "network": True, "parallelism": 2}
    first = assess_plan_necessity(operation)
    assert first == assess_plan_necessity(operation)
    assert first["plan_required"] is True
    assert first["estimate"]["billing"] == "unknown"
    assert first["estimate"]["cost_estimate_status"] == "unknown"


def test_governance_hashes_reject_non_json_finite_values() -> None:
    with pytest.raises(ValueError):
        artifact_hash({"max_cost_usd": float("nan")})
    with pytest.raises(ValueError):
        artifact_hash({"timeout_seconds": float("inf")})


def test_plan_assessment_reports_bounded_work_range_and_necessity() -> None:
    result = assess_plan_necessity({
        "action": "run_tests",
        "goal_requirement_ref": "goal:test-before-release",
        "necessity_reason": "Release requires a passing local suite.",
        "elapsed_time_minutes": {"minimum": 2, "likely": 5, "maximum": 12},
        "work_units": {
            "files_to_read": 3, "files_to_modify": 0, "tests_to_run": 18,
            "model_runs": 0, "benchmark_runs": 0,
        },
        "estimate_evidence_refs": ["repo:test-count"],
    })
    assert result["necessity"]["verdict"] == "required"
    assert result["estimate"]["elapsed_time_range"]["likely_minutes"] == 5
    assert result["estimate"]["work_units"]["tests_to_run"] == 18
    assert result["estimate"]["confidence"] == "high"
    assert result["recommended_decision"] == "approve_with_limits"


def test_operation_enforces_cost_ceiling_and_plan_reference() -> None:
    operation = {
        "action": "inspect_artifact", "paths": ["docs/result.md"],
        "sources": ["canonical"], "provider": "openai", "network": True,
        "estimated_cost_usd": 2.0,
    }
    codes = {issue["code"] for issue in validate_operation_scope(operation, packet())}
    assert COST_EXCEEDED in codes
    assert PLAN_REQUIRED in codes


def test_malformed_operation_fails_closed_instead_of_raising() -> None:
    issues = validate_operation_scope({}, packet())
    assert issues


def test_host_visualization_requires_scope_opt_in_and_plan() -> None:
    operation = {"action": "inspect_artifact", "capabilities": [HOST_VISUALIZATION]}
    missing = validate_operation_scope(operation, packet(capabilities=[HOST_VISUALIZATION]))
    codes = {issue["code"] for issue in missing}
    assert USER_OPT_IN_MISSING in codes
    assert PLAN_REQUIRED in codes
    allowed = packet(
        capabilities=[HOST_VISUALIZATION],
        opt_ins=[HOST_VISUALIZATION],
        plan_refs=["plan_visualization_approved"],
    )
    assert validate_operation_scope(operation, allowed) == []


def test_data_plot_permission_does_not_grant_host_visualization() -> None:
    scoped = packet(capabilities=[DATA_PLOT_GENERATION])
    plot = {"action": "inspect_artifact", "capabilities": [DATA_PLOT_GENERATION]}
    assert validate_operation_scope(plot, scoped) == []
    host = {"action": "inspect_artifact", "capabilities": [HOST_VISUALIZATION]}
    assert validate_operation_scope(host, scoped)


def test_controller_rejects_network_or_scope_hash_drift() -> None:
    scoped = packet(plan_refs=["plan_1"])
    scoped["scope"].update({"allow_network": False, "max_parallelism": 1})
    scoped["evidence_boundary"] = {"record_ids": []}
    scoped["failure_policy"] = {
        "stop": "blocking_only", "record": "ask", "detail": "redacted",
    }
    scoped["authority"]["scope_hash"] = task_scope_hash(scoped)
    operation = {
        "action": "inspect_artifact", "network": True,
        "scope_hash": scoped["authority"]["scope_hash"],
    }
    gate = operation_gate(operation, scoped)
    assert gate["allowed"] is False
    assert gate["controller_action"] == "reject_tool_call"
    operation["network"] = False
    operation["scope_hash"] = "sha256:forged"
    assert operation_gate(operation, scoped)["allowed"] is False


def test_failure_policy_resolves_each_field_task_profile_env_default() -> None:
    resolved = resolve_failure_policy(
        task={"failure_policy": {"record": "ask"}},
        profile={"failure_policy": {"stop": "current_step"}},
        environ={"URAG_FAILURE_DETAIL_LEVEL": "hashes_only"},
    )
    assert (resolved["stop"], resolved["record"], resolved["detail"]) == ("current_step", "ask", "hashes_only")
    assert resolved["sources"] == {
        "stop": "profile",
        "record": "task",
        "detail": "env:URAG_FAILURE_DETAIL_LEVEL",
    }


def test_every_failure_stops_immediately_and_ask_still_emits_tombstone() -> None:
    policy = resolve_failure_policy(
        task={"failure_policy": {"stop": "blocking_only", "record": "ask", "detail": "full"}},
        environ={},
    )
    failure = {
        "code": "NETWORK-TIMEOUT", "blocking": False, "run_id": "run_1",
        "workflow_id": "workflow_1", "agent_id": "retrieval_governor",
        "operation_id": "operation_1", "detail": {"api_key": "must-not-appear", "reason": "timeout"},
    }
    directive = failure_directive(failure, policy)
    assert directive["immediate_stop"] is True
    assert directive["block_new_operations"] is True
    assert directive["stop_scope"] == "current_step"
    assert directive["retry_allowed_before_record"] is False
    record = build_failure_record(failure, policy, occurred_at="2026-08-04T00:00:00+00:00")
    assert record["schema_version"] == "failure-tombstone/2.0"
    assert record["record_state"] == "awaiting_user_choice"
    assert record["requires_user_choice"] is True
    assert record["detail_sha256"].startswith("sha256:")
    assert "detail" not in record


def test_safe_defaults_and_critical_failures_follow_final_policy() -> None:
    policy = resolve_failure_policy(environ={})
    assert (policy["stop"], policy["record"], policy["detail"]) == (
        "blocking_only", "ask", "redacted",
    )
    directive = failure_directive(
        {"classification": "policy_violation", "blocking": False}, policy,
    )
    assert directive["stop_scope"] == "workflow"


def test_scientific_negative_result_is_preserved_as_research_result() -> None:
    policy = resolve_failure_policy(environ={})
    record = build_failure_record(
        {"classification": "scientific_negative_result", "blocking": False},
        policy,
        occurred_at="2026-08-04T00:00:00+00:00",
    )
    assert record["is_research_result"] is True
    assert record["stop_scope"] == "current_step"
    assert record["continuation_policy"] == "according_to_approved_research_plan"


def test_redacted_full_record_never_copies_secret_fields() -> None:
    policy = resolve_failure_policy(
        task={"failure_policy": {"record": "full", "detail": "redacted"}},
        environ={},
    )
    record = build_failure_record(
        {"code": "PROVIDER-401", "detail": {"api_key": "secret-value", "message": "denied"}},
        policy,
        occurred_at="2026-08-04T00:00:00+00:00",
    )
    assert record["detail"]["api_key"] == "[REDACTED]"
    assert record["detail"]["message"] == "denied"


def _governor_decision() -> dict:
    return {
        "agent_id": "scope_and_cost_governor",
        "status": "pass",
        "classification": {
            "reviewed_plan_hash": artifact_hash({"plan": "fixture"}),
            "necessity_verdict": "required",
            "difficulty": "medium",
            "estimate_confidence": "high",
            "scope_verdict": "within_approved_scope",
            "additional_work": "optional",
        },
        "decisions": [{
            "elapsed_time_range": {
                "minimum": "1m", "likely": "3m", "maximum": "10m",
            },
            "work_units": {"agent_calls": 2},
            "resource_cost": {"paid_api_usage_usd": 0},
            "assumptions": ["The task set remains unchanged."],
            "evidence_refs": [{"run_plan_hash": artifact_hash({"plan": "fixture"})}],
            "user_choice_required": False,
        }],
    }


def test_scope_governor_cross_field_contract_blocks_non_actionable_passes() -> None:
    valid = _governor_decision()
    assert validate_scope_governor_decision(
        valid,
        expected_plan_hash=valid["classification"]["reviewed_plan_hash"],
    ) == []

    mutations = (
        ("classification", "necessity_verdict", "out_of_scope"),
        ("classification", "scope_verdict", "reapproval_required"),
        ("classification", "additional_work", "required"),
        ("estimate", "user_choice_required", True),
        ("elapsed", "maximum", "unknown"),
    )
    for location, field, value in mutations:
        candidate = _governor_decision()
        if location == "classification":
            candidate["classification"][field] = value
        elif location == "estimate":
            candidate["decisions"][0][field] = value
        else:
            candidate["decisions"][0]["elapsed_time_range"][field] = value
        assert validate_scope_governor_decision(candidate), (location, field)
