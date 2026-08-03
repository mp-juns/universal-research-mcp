from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from adapters.markdown_views import render_plan_view, render_work_log_view
from core.amendments import resolve_core_amendments
from core.audit import audit_report
from core.proposals import append_approved_record


def base_record(record_id: str, kind: str, status: str = "completed") -> dict:
    return {
        "schema_version": "core/1.0",
        "record_id": record_id,
        "record_kind": kind,
        "study_id": "study_fixture",
        "occurred_at": "2026-08-03T10:00:00+09:00",
        "recorded_at": "2026-08-03T10:01:00+09:00",
        "status": status,
        "created_by": {"actor_id": "actor_researcher", "actor_type": "human"},
        "payload": {},
    }


class FrameworkOperationTests(unittest.TestCase):
    def test_completed_amendment_only_changes_resolved_view(self) -> None:
        original = base_record("obs_original", "observation")
        original["payload"] = {"summary": "recorded"}
        amendment = base_record("amendment_correct_summary", "amendment")
        amendment["relations"] = [{"type": "corrects", "target_id": "obs_original"}]
        amendment["payload"] = {"path": "/payload/summary", "recorded_value": "recorded", "corrected_value": "corrected", "reason": "transcription correction"}
        source_before = deepcopy(original)

        resolved, applied = resolve_core_amendments([original, amendment])

        self.assertEqual(original, source_before)
        self.assertEqual(resolved[0]["payload"]["summary"], "corrected")
        self.assertEqual(applied[0]["target_id"], "obs_original")

    def test_commit_requires_approval_and_is_append_only(self) -> None:
        record = base_record("obs_committed", "observation")
        record["approval_refs"] = ["approval_fixture"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            append_approved_record(path, record, "approval_fixture")
            first = path.read_text(encoding="utf-8")
            self.assertEqual(first.count("\n"), 1)
            with self.assertRaises(ValueError):
                append_approved_record(path, record, "approval_fixture")
            self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_audit_returns_record_addressable_approval_finding(self) -> None:
        session = base_record("session_unapproved", "execution_session", status="active")
        report = audit_report([session])
        self.assertEqual(report["finding_count"], 1)
        self.assertIn("record://session_unapproved/approval_refs", report["findings"][0]["evidence_refs"])

    def test_markdown_views_are_projections(self) -> None:
        plan = base_record("plan_fixture", "research_plan")
        plan["approval_refs"] = ["approval_fixture"]
        plan["payload"] = {"title": "Fixture plan", "objective": "Test projections", "in_scope": ["core"], "out_of_scope": ["network"]}
        work_log = base_record("obs_fixture", "observation")
        work_log["payload"] = {"observed": "Fixture result", "uncertainty": "No external data"}
        self.assertIn("## Objective", render_plan_view(plan))
        self.assertIn("### Observed", render_work_log_view(work_log))


if __name__ == "__main__":
    unittest.main()
