import unittest

from core.governance import (
    AGENT_IDS,
    active_agents,
    claim_gate,
    user_chat_report,
    validate_decision_record,
    validate_task_packet,
)
from core.index_refresh import refresh_eligibility, validate_index_health_record


def task_packet(agent_id: str = "retrieval_governor") -> dict:
    return {
        "run_id": "run_fixture",
        "agent_id": agent_id,
        "requester": "user",
        "purpose": "Validate fixture evidence boundaries.",
        "scope": {
            "allowed_paths": ["data/events"],
            "allowed_sources": ["canonical_ledger"],
            "allowed_actions": ["search", "fetch"],
            "forbidden_actions": ["edit", "execute"],
        },
        "evidence_boundary": {
            "result_ids": ["result_fixture"],
            "dataset_hashes": ["sha256:dataset"],
            "model_hashes": ["sha256:model"],
            "commit_ids": ["commit_fixture"],
        },
        "success_criteria": ["Return verified evidence."],
        "stop_conditions": ["Evidence is absent."],
    }


def decision_record() -> dict:
    return {
        "schema_version": "research-agent-decision-v1",
        "run_id": "run_fixture",
        "agent_id": "retrieval_governor",
        "status": "pass",
        "summary": "Evidence was retrieved.",
        "findings": [],
        "evidence": [],
        "commands": [],
        "decisions": [],
        "recommended_actions": [],
        "authority_used": ["search", "fetch"],
        "limitations": [],
        "attribution": {"requester": "user", "proposer": "user", "executor": "agent", "reviewer": ""},
    }


class GovernanceTests(unittest.TestCase):
    def test_exactly_eleven_fixed_roles(self) -> None:
        self.assertEqual(len(AGENT_IDS), 11)
        self.assertEqual(len(active_agents("lightweight")), 4)
        self.assertEqual(len(active_agents("benchmark")), 7)
        self.assertEqual(len(active_agents("final_review")), 7)
        self.assertEqual(len(active_agents("final_review", "main_result")), 11)
        for mode in ("lightweight", "benchmark", "final_review"):
            self.assertIn("scope_and_cost_governor", active_agents(mode))

    def test_critical_reviewer_cannot_activate_without_gate(self) -> None:
        packet = task_packet("cold_adversarial_reviewer")
        packet["scope"]["allowed_actions"] = ["review"]
        self.assertTrue(validate_task_packet(packet, "final_review"))
        self.assertEqual(validate_task_packet(packet, "final_review", "main_result"), [])

    def test_role_scope_rejects_execution_authority(self) -> None:
        packet = task_packet("analysis_objectivity_auditor")
        packet["scope"]["allowed_actions"] = ["inspect", "execute"]
        self.assertIn("exceeds analysis_objectivity_auditor authority: execute", validate_task_packet(packet, "benchmark")[0])

    def test_decision_must_match_task_and_scope(self) -> None:
        self.assertEqual(validate_decision_record(decision_record(), task_packet()), [])
        record = decision_record()
        record["authority_used"] = ["edit"]
        self.assertIn("exceeds task scope", validate_decision_record(record, task_packet())[0])

    def test_critical_or_high_findings_block_claims(self) -> None:
        decision = decision_record()
        decision["agent_id"] = "cold_adversarial_reviewer"
        decision["findings"] = [{
            "finding_id": "finding_1",
            "severity": "critical",
            "claim": "Comparator conditions differ.",
            "evidence_refs": [],
            "impact": "Comparison is not eligible.",
            "recommended_fix": "Disclose or rerun under equal conditions.",
            "confidence": "high",
        }]
        gate = claim_gate([decision])
        self.assertEqual(gate["claim_eligibility"], "blocked")
        self.assertTrue(gate["requires_user_decision"])

    def test_summary_chat_report_omits_internal_artifacts(self) -> None:
        report = user_chat_report(
            status="complete",
            outcome="Review finished.",
            artifact_refs=["docs/report.md"],
            executed_state="reviewed",
        )
        self.assertEqual(report["chat_disclosure"], "summary_only")
        self.assertNotIn("commands", report)
        self.assertNotIn("raw_logs", report)

    def test_only_recorded_research_events_are_refresh_eligible(self) -> None:
        event = {"record_id": "decision_fixture", "record_kind": "decision"}
        self.assertFalse(refresh_eligibility(event, canonical_recorded=False)["eligible"])
        self.assertTrue(refresh_eligibility(event, canonical_recorded=True)["eligible"])
        self.assertFalse(refresh_eligibility({"record_id": "plan_fixture", "record_kind": "research_plan"}, True)["eligible"])

    def test_succeeded_refresh_requires_retrieval_check(self) -> None:
        health = {
            "schema_version": "research-index-health-v1",
            "status": "succeeded",
            "index_revision": "index_fixture",
            "event_count": 1,
            "passage_count": 1,
            "embedding_model": "lexical-only",
            "embedding_dimension": 0,
            "artifact_hashes": [],
            "source_event_ids": ["decision_fixture"],
            "retrieval_verification": {"status": "not_run"},
            "failures": [],
        }
        self.assertIn("a succeeded refresh requires passed retrieval verification", validate_index_health_record(health))
        health["retrieval_verification"] = {"status": "passed"}
        self.assertEqual(validate_index_health_record(health), [])


if __name__ == "__main__":
    unittest.main()
