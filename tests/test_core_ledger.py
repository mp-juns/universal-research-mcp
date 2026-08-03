import json
from copy import deepcopy
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker

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

    def test_manual_validator_matches_schema_contract_cases(self) -> None:
        schema = json.loads((ROOT / "schemas/core-record.schema.json").read_text(encoding="utf-8"))
        schema_validator = Draft202012Validator(
            schema, format_checker=FormatChecker(formats=["date-time"])
        )
        base = json.loads((ROOT / "fixtures/core/valid-core-record.jsonl").read_text(encoding="utf-8"))
        cases: list[tuple[str, dict]] = [("valid", base)]

        unexpected_top_level = deepcopy(base)
        unexpected_top_level["undeclared"] = True
        cases.append(("unexpected top-level", unexpected_top_level))
        malformed_identifier = deepcopy(base)
        malformed_identifier["record_id"] = "not valid"
        cases.append(("record id", malformed_identifier))
        malformed_timestamp = deepcopy(base)
        malformed_timestamp["occurred_at"] = "not-a-date"
        cases.append(("date-time", malformed_timestamp))
        unexpected_actor_field = deepcopy(base)
        unexpected_actor_field["created_by"]["role"] = "reviewer"
        cases.append(("actor additional property", unexpected_actor_field))
        malformed_relation = deepcopy(base)
        malformed_relation["relations"][0]["target"] = "protocol_fixture_1"
        cases.append(("relation additional property", malformed_relation))
        malformed_evidence = deepcopy(base)
        del malformed_evidence["source_refs"][0]["verification_status"]
        cases.append(("evidence required field", malformed_evidence))
        malformed_approval_ref = deepcopy(base)
        malformed_approval_ref["approval_refs"] = ["not-an-approval"]
        cases.append(("approval ref", malformed_approval_ref))

        for name, record in cases:
            with self.subTest(name=name):
                schema_accepts = not list(schema_validator.iter_errors(record))
                manual_accepts = not validate_core_record(record, {"protocol_fixture_1"})
                self.assertEqual(manual_accepts, schema_accepts)


if __name__ == "__main__":
    unittest.main()
