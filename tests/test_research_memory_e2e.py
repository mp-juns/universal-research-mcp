import json
import hashlib
import os
from pathlib import Path
import runpy
import sqlite3
import tempfile
import unittest

from scripts.build_research_ledger_index import build


class ResearchMemoryEndToEndTests(unittest.TestCase):
    def test_fixture_ledger_to_search_fetch_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events_root = root / "data/events"
            source_path = root / "docs/evidence.md"
            source_path.parent.mkdir(parents=True)
            source_path.write_text("# Verified evidence\n\nApproval was recorded before the fixture session.\n", encoding="utf-8")
            source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            (events_root / "daily/2026-08-04").mkdir(parents=True)
            (events_root / "sources.jsonl").write_text(
                json.dumps({"source_id": "src_fixture", "source_path": "docs/evidence.md", "source_sha256": source_sha256, "source_type": "markdown", "legacy_import": False}) + "\n",
                encoding="utf-8",
            )
            event = {
                "schema_version": "core/1.0",
                "record_id": "claim_fixture_approval",
                "record_kind": "claim",
                "study_id": "study_fixture",
                "occurred_at": "2026-08-04T10:00:00+09:00",
                "recorded_at": "2026-08-04T10:01:00+09:00",
                "status": "completed",
                "created_by": {"actor_id": "actor_researcher", "actor_type": "human"},
                "relations": [{"type": "uses_protocol", "target_id": "protocol_fixture"}],
                "source_refs": [{"artifact_revision_id": f"artifact_evidence@sha256:{source_sha256}", "locator": {"kind": "line_range", "path": "docs/evidence.md", "start": 1, "end": 3, "heading": "Verified evidence"}, "verification_status": "human_verified"}],
                "artifact_refs": ["artifact_evidence"],
                "payload": {"statement": "Approval recorded before fixture session", "support_status": "supported", "observed": {"statement": "Approval was recorded before the fixture session."}},
            }
            protocol = {
                "schema_version": "core/1.0",
                "record_id": "protocol_fixture",
                "record_kind": "protocol",
                "study_id": "study_fixture",
                "occurred_at": "2026-08-04T09:00:00+09:00",
                "recorded_at": "2026-08-04T09:01:00+09:00",
                "status": "completed",
                "created_by": {"actor_id": "actor_researcher", "actor_type": "human"},
                "payload": {"summary": "Fixture protocol"},
            }
            (events_root / "daily/2026-08-04/events.jsonl").write_text(
                json.dumps(protocol) + "\n" + json.dumps(event) + "\n",
                encoding="utf-8",
            )
            lexical_db = root / "data/index/research.sqlite"
            build(events_root, lexical_db)
            index = sqlite3.connect(lexical_db)
            try:
                relation = index.execute(
                    "SELECT relation_type, target FROM relations WHERE event_id = ?",
                    ("claim_fixture_approval",),
                ).fetchone()
                raw_json = index.execute(
                    "SELECT raw_json FROM events WHERE event_id = ?",
                    ("claim_fixture_approval",),
                ).fetchone()[0]
            finally:
                index.close()
            self.assertEqual(relation, ("uses_protocol", "protocol_fixture"))
            preserved = json.loads(raw_json)
            self.assertEqual(preserved["relations"][0]["target_id"], "protocol_fixture")
            self.assertEqual(preserved["source_refs"][0]["artifact_revision_id"], f"artifact_evidence@sha256:{source_sha256}")

            prior_environment = dict(os.environ)
            try:
                os.environ["UNIVERSAL_RESEARCH_ROOT"] = str(root)
                os.environ["UNIVERSAL_RESEARCH_LEXICAL_DB"] = str(lexical_db)
                os.environ["UNIVERSAL_RESEARCH_EVENTS_ROOT"] = str(events_root)
                loaded = runpy.run_path(str(Path(__file__).resolve().parents[1] / "mcp/research_memory/mcp_server.py"))
                candidates = loaded["search_lexical"]("approval", 5)
                evidence = loaded["memory_fetch_evidence"]("docs/evidence.md", 1, 3, 0)
                audit = loaded["memory_audit_ledger"]()
            finally:
                os.environ.clear()
                os.environ.update(prior_environment)

            self.assertEqual(candidates[0]["event_id"], "claim_fixture_approval")
            self.assertIn("Approval was recorded", evidence["content"])
            self.assertEqual(evidence["indexed_sha256"], source_sha256)
            self.assertEqual(evidence["current_sha256"], source_sha256)
            self.assertEqual(evidence["sha256"], source_sha256)
            self.assertEqual(evidence["integrity_status"], "matched")
            self.assertEqual(audit["finding_count"], 0)

            source_path.write_text("# Changed evidence\n", encoding="utf-8")
            second_prior_environment = dict(os.environ)
            try:
                os.environ["UNIVERSAL_RESEARCH_ROOT"] = str(root)
                os.environ["UNIVERSAL_RESEARCH_LEXICAL_DB"] = str(lexical_db)
                loaded = runpy.run_path(str(Path(__file__).resolve().parents[1] / "mcp/research_memory/mcp_server.py"))
                changed = loaded["memory_fetch_evidence"]("docs/evidence.md", 1, 1, 0)
            finally:
                os.environ.clear()
                os.environ.update(second_prior_environment)
            self.assertEqual(changed["indexed_sha256"], source_sha256)
            self.assertNotEqual(changed["current_sha256"], source_sha256)
            self.assertEqual(changed["integrity_status"], "mismatched")


if __name__ == "__main__":
    unittest.main()
