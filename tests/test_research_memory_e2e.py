import json
import os
from pathlib import Path
import runpy
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
            (events_root / "daily/2026-08-04").mkdir(parents=True)
            (events_root / "sources.jsonl").write_text(
                json.dumps({"source_id": "src_fixture", "source_path": "docs/evidence.md", "source_sha256": "fixture", "source_type": "markdown", "legacy_import": False}) + "\n",
                encoding="utf-8",
            )
            event = {
                "event_id": "evt_fixture_approval",
                "date": "2026-08-04",
                "event_type": "decision",
                "status": "completed",
                "project": "fixture",
                "workstream": "governance",
                "summary": "Approval recorded before fixture session",
                "relations": [],
                "artifacts": [{"path": "docs/evidence.md", "sha256": "fixture", "role": "evidence"}],
                "source": {"source_path": "docs/evidence.md", "source_sha256": "fixture", "heading": "Verified evidence", "line_start": 1, "line_end": 3, "legacy_import": False, "requires_human_review": False},
            }
            (events_root / "daily/2026-08-04/events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
            lexical_db = root / "data/index/research.sqlite"
            build(events_root, lexical_db)

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

            self.assertEqual(candidates[0]["event_id"], "evt_fixture_approval")
            self.assertIn("Approval was recorded", evidence["content"])
            self.assertEqual(audit["finding_count"], 0)


if __name__ == "__main__":
    unittest.main()
