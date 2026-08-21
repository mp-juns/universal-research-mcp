import unittest
from datetime import datetime, timedelta, timezone

from universal_research_mcp.governance.escalation import evaluate_gate
from universal_research_mcp.governance.hashing import artifact_hash, hash_without
from universal_research_mcp.governance.registry import CRITICAL, FIXED_ROSTER, load_registry, manifest_hash, validate_registry
from universal_research_mcp.governance.scope_policy import task_scope_hash
from universal_research_mcp.governance.validation import validate_decision, validate_task_packet
from universal_research_mcp.governance.workflow import initial_state, transition


def packet() -> dict:
    registry = load_registry()
    manifest = registry["retrieval_governor"]
    scope = {
        "allowed_paths": ["data/events"],
        "allowed_sources": ["canonical"],
        "allowed_actions": ["research_search", "research_fetch", "emit_decision"],
        "forbidden_actions": manifest["authority"]["forbidden_actions"],
        "allowed_capabilities": [], "allowed_providers": [],
        "allow_network": False, "allow_model_execution": False,
        "allow_benchmark": False, "allow_background": False,
        "max_parallelism": 1, "estimated_cost_usd": 0, "max_cost_usd": 0,
    }
    evidence = {
        "record_ids": ["decision_fixture"], "result_ids": [], "dataset_hashes": [],
        "model_hashes": [], "artifact_revisions": [], "commit_ids": [], "snapshot_hash": "sha256:snapshot",
    }
    value = {
        "schema_version": "research-agent-task/1.0", "governance_version": "agent-governance/2.0",
        "run_id": "run_fixture",
        "workflow_id": "workflow_fixture", "agent_id": "retrieval_governor",
        "requester": {"type": "user", "id": "actor_user"}, "purpose": "Verify evidence.",
        "mode": "lightweight", "scope": scope, "evidence_boundary": evidence,
        "agent_creation_disclosure": {
            "schema_version": "agent-creation-disclosure/1.0",
            "reason": "Use one bounded governance fixture agent.",
            "delegated_tasks": ["Verify the bounded fixture evidence."],
            "agent_count": 1,
            "direct_execution_alternative": "Verify the fixture directly.",
            "expected_additional_tokens": {
                "minimum": 0, "likely": 100, "maximum": 1000,
            },
            "expected_elapsed_minutes": {
                "minimum": 0, "likely": 1, "maximum": 5,
            },
            "scope": {
                "paths": ["data/events"], "network": False,
                "model_execution": True, "writes": False,
            },
        },
        "authority": {
            "approval_refs": [], "authority_basis": "read-only review",
            "plan_refs": [], "user_opt_ins": [], "scope_hash": "pending",
        },
        "failure_policy": {"stop": "blocking_only", "record": "ask", "detail": "redacted"},
        "success_criteria": ["Evidence is verified."], "stop_conditions": ["Evidence is missing."],
        "role_manifest_hash": manifest_hash(manifest), "created_at": "2026-08-04T10:00:00+00:00",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    value["authority"]["scope_hash"] = task_scope_hash(value)
    return value


def decision(task: dict) -> dict:
    registry = load_registry()
    value = {
        "schema_version": "research-agent-decision/1.0", "run_id": task["run_id"],
        "workflow_id": task["workflow_id"], "agent_id": task["agent_id"],
        "role_manifest_hash": manifest_hash(registry[task["agent_id"]]), "task_packet_hash": artifact_hash(task),
        "status": "pass", "summary": "Evidence was retrieved.",
        "classification": {"analysis_type": "descriptive", "claim_eligibility": "eligible"},
        "findings": [], "evidence": [], "commands": [], "decisions": [], "recommended_actions": [],
        "authority_used": ["research_search", "research_fetch"], "limitations": [],
        "attribution": {"requester": "user", "proposer": "user", "executor": "agent", "reviewer": ""},
        "started_at": "2026-08-04T10:00:00+00:00", "completed_at": "2026-08-04T10:01:00+00:00",
    }
    value["output_hash"] = hash_without(value, "output_hash")
    return value


class UragGovernanceTests(unittest.TestCase):
    def test_registry_has_the_exact_fixed_roster(self) -> None:
        registry = load_registry()
        self.assertEqual(set(registry), FIXED_ROSTER)
        self.assertEqual(len(registry), 11)
        self.assertEqual(registry["scope_and_cost_governor"]["schema_version"], "agent-governance/2.0")
        self.assertEqual(validate_registry(registry), [])
        for agent_id in CRITICAL:
            self.assertNotIn("edit_derived_artifact", registry[agent_id]["authority"]["allowed_actions"])

    def test_packet_hash_and_role_hash_fail_closed(self) -> None:
        valid = packet()
        self.assertEqual(validate_task_packet(valid), [])
        invalid = packet()
        invalid["authority"]["scope_hash"] = "sha256:forged"
        self.assertEqual(validate_task_packet(invalid)[0]["code"], "GOV-SCOPE-001")

    def test_write_action_requires_approval(self) -> None:
        writable = packet()
        writable["agent_id"] = "correction_executor"
        manifest = load_registry()["correction_executor"]
        writable["mode"] = "benchmark"
        writable["role_manifest_hash"] = manifest_hash(manifest)
        writable["scope"]["allowed_actions"] = ["edit_derived_artifact"]
        writable["scope"]["forbidden_actions"] = manifest["authority"]["forbidden_actions"]
        writable["authority"]["scope_hash"] = task_scope_hash(writable)
        self.assertIn("GOV-APPROVAL-001", [issue["code"] for issue in validate_task_packet(writable)])

    def test_decision_cannot_be_tampered_with(self) -> None:
        task = packet()
        valid = decision(task)
        self.assertEqual(validate_decision(valid, task), [])
        valid["summary"] = "Modified after signing."
        self.assertIn("GOV-OUTPUT-001", [issue["code"] for issue in validate_decision(valid, task)])

    def test_workflow_only_allows_next_stage_or_exception(self) -> None:
        state = initial_state("benchmark")
        with self.assertRaises(ValueError):
            transition(state, "authorized_work", "skip approval")
        state = transition(state, "scope_cost_review", "preflight started")
        state = transition(state, "awaiting_plan_approval", "plan complete")
        self.assertEqual(state["stage"], "awaiting_plan_approval")
        self.assertEqual(transition(state, "blocked", "evidence missing")["stage"], "blocked")

    def test_high_and_critical_findings_block_gate(self) -> None:
        result = evaluate_gate([{"agent_id": "cold_adversarial_reviewer", "status": "pass", "findings": [{"finding_id": "finding_1", "severity": "critical"}]}])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["blockers"][0]["code"], "GOV-GATE-001")


if __name__ == "__main__":
    unittest.main()
