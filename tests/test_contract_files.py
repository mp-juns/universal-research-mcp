import json
from pathlib import Path
import unittest

from governance.registry import FIXED_ROSTER


ROOT = Path(__file__).resolve().parents[1]


class ContractFileTests(unittest.TestCase):
    def test_schema_files_are_valid_json(self) -> None:
        for name in (
            "core-record.schema.json", "pack-manifest.schema.json", "project-profile.schema.json",
            "research-agent-task.schema.json", "research-agent-decision.schema.json", "index-health.schema.json",
            "agent-runtime-event.schema.json", "governance-operation.schema.json",
        ):
            with self.subTest(name=name):
                schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_pack_declares_non_relaxing_blocking_constraints(self) -> None:
        content = (ROOT / "packs/study_type/research_operations.yaml").read_text(encoding="utf-8")
        self.assertIn("explicit_approval_before_execution", content)
        self.assertIn("source_grounded_claims", content)

    def test_governance_prompt_pack_schema_and_role_files_cover_fixed_roster(self) -> None:
        schema = json.loads(
            (ROOT / "governance/schemas/prompt-pack.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(
            set(schema["properties"]["agent_id"]["enum"]), FIXED_ROSTER
        )

        roles_root = ROOT / "governance/roles"
        role_ids = {
            path.parent.name for path in roles_root.glob("*/role.yaml")
        }
        instruction_ids = {
            path.parent.name for path in roles_root.glob("*/instructions.md")
        }
        self.assertEqual(role_ids, FIXED_ROSTER)
        self.assertEqual(instruction_ids, FIXED_ROSTER)


if __name__ == "__main__":
    unittest.main()
