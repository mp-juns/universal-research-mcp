import json
from pathlib import Path
import unittest

from core.ledger import validate_core_record, validate_records


ROOT = Path(__file__).resolve().parents[1]


class CoreLedgerTests(unittest.TestCase):
    def test_valid_fixture_passes_core_validation(self) -> None:
        row = json.loads((ROOT / "fixtures/core/valid-core-record.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(validate_core_record(row, {"protocol_fixture_1"}), [])

    def test_active_session_without_approval_is_blocking(self) -> None:
        record = {
            "schema_version": "core/1.0",
            "record_id": "session_fixture_1",
            "record_kind": "execution_session",
            "occurred_at": "2026-08-03T10:00:00+09:00",
            "recorded_at": "2026-08-03T10:01:00+09:00",
            "status": "active",
            "created_by": {"actor_id": "actor_ai", "actor_type": "ai"},
            "payload": {},
        }
        messages = [issue.message for issue in validate_core_record(record)]
        self.assertIn("active session requires explicit approval", messages)

    def test_supported_claim_requires_human_verified_evidence(self) -> None:
        record = {
            "schema_version": "core/1.0",
            "record_id": "claim_fixture_1",
            "record_kind": "claim",
            "occurred_at": "2026-08-03T10:00:00+09:00",
            "recorded_at": "2026-08-03T10:01:00+09:00",
            "status": "completed",
            "created_by": {"actor_id": "actor_ai", "actor_type": "ai"},
            "source_refs": [],
            "payload": {"support_status": "supported"},
        }
        messages = [issue.message for issue in validate_core_record(record)]
        self.assertIn("supported claim requires human-verified evidence", messages)

    def test_duplicate_legacy_event_ids_are_rejected(self) -> None:
        event = {"event_id": "evt_duplicate", "date": "2026-08-03", "event_type": "note", "status": "completed", "project": "fixture", "summary": "fixture"}
        messages = [issue.message for issue in validate_records([event, event])]
        self.assertIn("duplicate record identifier", messages)


if __name__ == "__main__":
    unittest.main()
