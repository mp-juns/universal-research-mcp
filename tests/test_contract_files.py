import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContractFileTests(unittest.TestCase):
    def test_schema_files_are_valid_json(self) -> None:
        for name in ("core-record.schema.json", "pack-manifest.schema.json", "project-profile.schema.json"):
            with self.subTest(name=name):
                schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_pack_declares_non_relaxing_blocking_constraints(self) -> None:
        content = (ROOT / "packs/study_type/research_operations.yaml").read_text(encoding="utf-8")
        self.assertIn("explicit_approval_before_execution", content)
        self.assertIn("source_grounded_claims", content)


if __name__ == "__main__":
    unittest.main()
