import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from universal_research_mcp.governance.hashing import artifact_hash, hash_without
from universal_research_mcp.governance.registry import CRITICAL, load_registry, manifest_hash
from universal_research_mcp.governance.scope_policy import task_scope_hash
from universal_research_mcp.governance.validation import validate_task_packet
from universal_research_mcp.integrations.codex.adapter import (
    build_critical_review_batch,
    build_dispatch_request,
    build_scope_governor_receipt,
    capture_decision,
    serialize_dispatch_manifest,
    validate_dispatch_manifest,
)


def packet(agent_id: str = "retrieval_governor", mode: str = "lightweight", snapshot: str = "sha256:snapshot") -> dict:
    manifest = load_registry()[agent_id]
    scope = {
        "allowed_paths": ["data/events"], "allowed_sources": ["canonical"],
        "allowed_actions": list(manifest["authority"]["allowed_actions"][:1]),
        "forbidden_actions": manifest["authority"]["forbidden_actions"],
        "allowed_capabilities": [], "allowed_providers": [],
        "allow_network": False, "allow_model_execution": False,
        "allow_benchmark": False, "allow_background": False,
        "max_parallelism": 1, "estimated_cost_usd": 0, "max_cost_usd": 0,
    }
    boundary = {"record_ids": ["decision_fixture"], "result_ids": [], "dataset_hashes": [], "model_hashes": [], "artifact_revisions": [], "commit_ids": [], "snapshot_hash": snapshot}
    agent_count = len(CRITICAL) if mode == "final_review" and agent_id in CRITICAL else 1
    disclosure = {
        "schema_version": "agent-creation-disclosure/1.0",
        "reason": "Use isolated evidence-bound review agents for this approved workflow.",
        "delegated_tasks": [f"Perform governed review task {index + 1}." for index in range(agent_count)],
        "agent_count": agent_count,
        "direct_execution_alternative": "The host could perform the same review sequentially without delegated agents.",
        "expected_additional_tokens": {"minimum": 0, "likely": 1000, "maximum": 10_000},
        "expected_elapsed_minutes": {"minimum": 1, "likely": 5, "maximum": 30},
        "scope": {
            "paths": ["data/events"], "network": False,
            "model_execution": True, "writes": False,
        },
    }
    value = {
        "schema_version": "research-agent-task/1.0", "governance_version": "agent-governance/2.0",
        "run_id": "run_fixture", "workflow_id": "workflow_fixture",
        "agent_id": agent_id, "requester": {"type": "workflow", "id": "host"}, "purpose": "Review evidence.",
        "mode": mode, "scope": scope, "evidence_boundary": boundary,
        "agent_creation_disclosure": disclosure,
        "authority": {
            "approval_refs": ["approval_agent_creation"], "authority_basis": "review",
            "plan_refs": [], "user_opt_ins": ["agent_creation"], "scope_hash": "pending",
        },
        "failure_policy": {"stop": "blocking_only", "record": "ask", "detail": "redacted"},
        "success_criteria": ["Return a decision."], "stop_conditions": ["Evidence missing."],
        "role_manifest_hash": manifest_hash(manifest), "created_at": "2026-08-04T10:00:00+00:00",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    value["authority"]["scope_hash"] = task_scope_hash(value)
    return value


def passing_decision(task: dict) -> dict:
    classification = {}
    decisions = []
    if task["agent_id"] == "scope_and_cost_governor":
        classification = {
            "reviewed_plan_hash": artifact_hash({"task": artifact_hash(task)}),
            "necessity_verdict": "required",
            "difficulty": "low",
            "estimate_confidence": "high",
            "scope_verdict": "within_approved_scope",
            "additional_work": "optional",
        }
        decisions = [{
            "elapsed_time_range": {"minimum": "1m", "likely": "2m", "maximum": "5m"},
            "work_units": {"agent_calls": 1},
            "resource_cost": {"paid_api_usage_usd": 0},
            "assumptions": ["The fixture packet is immutable."],
            "evidence_refs": [{"task_packet_hash": artifact_hash(task)}],
            "user_choice_required": False,
        }]
    value = {
        "schema_version": "research-agent-decision/1.0",
        "run_id": task["run_id"],
        "workflow_id": task["workflow_id"],
        "agent_id": task["agent_id"],
        "role_manifest_hash": task["role_manifest_hash"],
        "task_packet_hash": artifact_hash(task),
        "status": "pass",
        "summary": "Scope is bounded.",
        "classification": classification,
        "findings": [],
        "evidence": [],
        "commands": [],
        "decisions": decisions,
        "recommended_actions": [],
        "authority_used": [],
        "limitations": [],
        "attribution": {
            "requester": "user", "proposer": "host", "executor": "agent", "reviewer": "",
        },
        "started_at": "2026-08-04T10:00:00+00:00",
        "completed_at": "2026-08-04T10:01:00+00:00",
    }
    value["output_hash"] = hash_without(value, "output_hash")
    return value


def scope_receipt(tasks: list[dict], mode: str = "lightweight") -> dict:
    governor = packet("scope_and_cost_governor", mode)
    captured = capture_decision(governor, passing_decision(governor))
    result = build_scope_governor_receipt(governor, captured, tasks)
    assert result["valid"]
    return result["receipt"]


class CodexAdapterTests(unittest.TestCase):
    def test_common_task_validator_requires_agent_creation_disclosure(self) -> None:
        task = packet()
        task.pop("agent_creation_disclosure")

        issues = validate_task_packet(task)

        self.assertTrue(any(
            "agent_creation_disclosure" in issue["message"] for issue in issues
        ))

    def test_dispatch_preserves_only_packet_granted_permissions(self) -> None:
        task = packet()
        self.assertFalse(build_dispatch_request(task)["dispatchable"])
        receipt = scope_receipt([task])
        request = build_dispatch_request(task, receipt)
        self.assertTrue(request["dispatchable"])
        self.assertTrue(request["execution"]["host_dispatch_required"])
        self.assertEqual(request["execution"]["model_selection"], "host_owned")
        self.assertEqual(request["role_instructions"]["allowed_actions"], ["research_search"])
        tampered = dict(receipt)
        tampered["governor_status"] = "fail"
        self.assertFalse(build_dispatch_request(task, tampered)["dispatchable"])

    def test_invalid_packet_never_becomes_dispatchable(self) -> None:
        invalid = packet()
        invalid["role_manifest_hash"] = "sha256:forged"
        self.assertFalse(build_dispatch_request(invalid)["dispatchable"])

    def test_dispatch_requires_explanation_and_explicit_agent_creation_approval(self) -> None:
        task = packet()
        receipt = scope_receipt([task])
        for mutation in ("disclosure", "opt_in", "approval"):
            invalid = deepcopy(task)
            if mutation == "disclosure":
                invalid.pop("agent_creation_disclosure")
            elif mutation == "opt_in":
                invalid["authority"]["user_opt_ins"] = []
            else:
                invalid["authority"]["approval_refs"] = []
            invalid["authority"]["scope_hash"] = task_scope_hash(invalid)
            result = build_dispatch_request(invalid, receipt)
            self.assertFalse(result["dispatchable"])
            self.assertTrue(any("agent creation" in issue["message"] for issue in result["issues"]))

    def test_single_dispatch_rejects_inflated_agent_count(self) -> None:
        task = packet()
        task["agent_creation_disclosure"]["agent_count"] = 2
        task["agent_creation_disclosure"]["delegated_tasks"] = [
            "Perform governed review task 1.",
            "Perform governed review task 2.",
        ]
        task["authority"]["scope_hash"] = task_scope_hash(task)

        result = build_dispatch_request(task, scope_receipt([task]))

        self.assertFalse(result["dispatchable"])
        self.assertTrue(any("agent_count" in issue["message"] for issue in result["issues"]))

    def test_dispatch_rejects_disclosure_that_hides_packet_scope(self) -> None:
        task = packet()
        task["agent_creation_disclosure"]["scope"]["paths"] = []
        task["authority"]["scope_hash"] = task_scope_hash(task)
        result = build_dispatch_request(task, scope_receipt([task]))

        self.assertFalse(result["dispatchable"])
        self.assertTrue(any("paths do not match" in issue["message"] for issue in result["issues"]))

    def test_critical_batch_is_isolated_and_snapshot_bound(self) -> None:
        tasks = [packet(agent_id, "final_review") for agent_id in CRITICAL]
        batch = build_critical_review_batch(tasks, scope_receipt(tasks, "final_review"))
        self.assertTrue(batch["dispatchable"])
        self.assertEqual(len(batch["requests"]), 4)
        self.assertTrue(all(item["execution"]["isolated_context"] for item in batch["requests"]))
        mismatch = [packet(agent_id, "final_review", "sha256:other" if agent_id == "substance_reviewer" else "sha256:snapshot") for agent_id in CRITICAL]
        self.assertFalse(build_critical_review_batch(mismatch)["dispatchable"])

    def test_invalid_decision_is_retained_not_promoted(self) -> None:
        task = packet()
        invalid = {"summary": "free text only"}
        captured = capture_decision(task, invalid)
        self.assertFalse(captured["accepted"])
        self.assertEqual(captured["artifact_kind"], "invalid_agent_output")

    def test_scope_receipt_revalidates_captured_decision_and_hash(self) -> None:
        governor = packet("scope_and_cost_governor")
        worker = packet("retrieval_governor")
        captured = capture_decision(governor, passing_decision(governor))
        captured["decision_hash"] = "sha256:" + "0" * 64

        result = build_scope_governor_receipt(governor, captured, [worker])

        self.assertFalse(result["valid"])
        self.assertTrue(any("captured decision hash" in issue["message"] for issue in result["issues"]))

    def test_dispatch_manifest_is_deterministic_and_nonexecuting(self) -> None:
        task = packet()
        request = build_dispatch_request(task, scope_receipt([task]))
        pinned_hash = request["dispatch_hash"]
        first = serialize_dispatch_manifest(request, expected_manifest_hash=pinned_hash)
        second = serialize_dispatch_manifest(request, expected_manifest_hash=pinned_hash)
        self.assertEqual(first, second)
        self.assertIn('"host_dispatch_required":true', first)
        self.assertEqual(request["dispatch_hash"], hash_without(request, "dispatch_hash"))
        self.assertEqual(
            validate_dispatch_manifest(request, expected_manifest_hash=pinned_hash),
            [],
        )
        with self.assertRaises(ValueError):
            serialize_dispatch_manifest(
                {"dispatchable": False},
                expected_manifest_hash="sha256:" + "0" * 64,
            )

    def test_dispatch_manifest_rejects_post_build_authority_mutation(self) -> None:
        task = packet()
        original_task_hash = artifact_hash(task)
        request = build_dispatch_request(task, scope_receipt([task]))
        pinned_hash = request["dispatch_hash"]
        request["role_instructions"]["allowed_actions"].append(
            "edit_derived_artifact",
        )

        self.assertEqual(artifact_hash(task), original_task_hash)
        self.assertNotIn(
            "edit_derived_artifact",
            task["scope"]["allowed_actions"],
        )

        with self.assertRaisesRegex(ValueError, "integrity hash mismatch"):
            serialize_dispatch_manifest(request, expected_manifest_hash=pinned_hash)

        request["dispatch_hash"] = hash_without(request, "dispatch_hash")
        issues = validate_dispatch_manifest(
            request,
            expected_manifest_hash=pinned_hash,
        )
        self.assertTrue(any("outside role authority" in issue["message"] for issue in issues))
        with self.assertRaisesRegex(ValueError, "outside role authority"):
            serialize_dispatch_manifest(request, expected_manifest_hash=pinned_hash)

    def test_critical_batch_is_sealed_and_revalidated_before_export(self) -> None:
        tasks = [packet(agent_id, "final_review") for agent_id in CRITICAL]
        batch = build_critical_review_batch(tasks, scope_receipt(tasks, "final_review"))
        pinned_hash = batch["batch_hash"]

        self.assertEqual(
            validate_dispatch_manifest(batch, expected_manifest_hash=pinned_hash),
            [],
        )
        serialize_dispatch_manifest(batch, expected_manifest_hash=pinned_hash)
        batch["requests"][0]["execution"]["network"] = "granted"

        with self.assertRaisesRegex(ValueError, "integrity hash mismatch"):
            serialize_dispatch_manifest(batch, expected_manifest_hash=pinned_hash)

    def test_dispatch_validator_fails_closed_on_malformed_nested_types(self) -> None:
        task = packet()
        request = build_dispatch_request(task, scope_receipt([task]))
        pinned_hash = request["dispatch_hash"]
        request["role_instructions"]["allowed_actions"] = [{"command": "hidden"}]
        request["dispatch_hash"] = hash_without(request, "dispatch_hash")

        issues = validate_dispatch_manifest(
            request,
            expected_manifest_hash=pinned_hash,
        )

        self.assertTrue(issues)
        self.assertTrue(any("outside role authority" in issue["message"] for issue in issues))


if __name__ == "__main__":
    unittest.main()
