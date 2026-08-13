import json
from pathlib import Path
import tempfile
import tomllib
import unittest
import zipfile

from universal_research_mcp.governance.registry import FIXED_ROSTER
from universal_research_mcp.tools.distribution import (
    BUNDLE_PREFIX,
    GOVERNANCE_ROLE_IDS,
    REQUIRED_BUNDLE_FILES,
    REQUIRED_GOVERNANCE_FILES,
    REQUIRED_RUNTIME_FILES,
    validate_wheel,
)


ROOT = Path(__file__).resolve().parents[1]


class PublicDistributionTests(unittest.TestCase):
    def test_package_exposes_stable_mcp_entry_point(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        self.assertEqual(project["project"]["name"], "universal-research-mcp")
        self.assertEqual(project["project"]["version"], "0.4.2")
        self.assertEqual(project["project"]["license"], "MIT")
        self.assertEqual(project["project"]["license-files"], ["LICENSE"])
        self.assertEqual(project["project"]["authors"], [{"name": "mp-juns"}])
        self.assertEqual(
            project["project"]["scripts"]["universal-research-mcp"],
            "universal_research_mcp.cli:legacy_main",
        )
        self.assertEqual(
            project["project"]["scripts"]["universal-research"],
            "universal_research_mcp.cli:main",
        )
        self.assertNotIn(
            "universal-research-agent-runtime",
            project["project"]["scripts"],
        )
        self.assertIn("MIT License", (ROOT / "LICENSE").read_text(encoding="utf-8"))

    def test_distribution_declares_schema_pack_and_plugin_bundle(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        data_files = project["tool"]["setuptools"]["data-files"]
        package_data = project["tool"]["setuptools"]["package-data"]["universal_research_mcp.governance"]
        self.assertIn("share/universal-research-mcp/docs", data_files)
        self.assertIn("share/universal-research-mcp/schemas", data_files)
        self.assertIn("share/universal-research-mcp/packs/study_type", data_files)
        self.assertIn(
            "share/universal-research-mcp/plugin/universal-research-memory",
            data_files,
        )
        self.assertIn("roles/*/role.yaml", package_data)
        self.assertIn("roles/*/instructions.md", package_data)
        self.assertIn("schemas/*.json", package_data)
        self.assertEqual(
            set(data_files["share/universal-research-mcp/docs"]),
            {
                "docs/failure-policy.md",
                "docs/host-integration.md",
                "docs/security.md",
            },
        )

    def test_wheel_validator_requires_every_role_prompt_pack_member(self) -> None:
        self.assertEqual(set(GOVERNANCE_ROLE_IDS), FIXED_ROSTER)
        self.assertIn("universal_research_mcp/integrations/codex/adapter.py", REQUIRED_RUNTIME_FILES)
        self.assertNotIn("universal_research_mcp/runtime_server.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("docs/host-integration.md", REQUIRED_BUNDLE_FILES)
        self.assertNotIn("docs/agent-runtime.md", REQUIRED_BUNDLE_FILES)
        self.assertEqual(
            {
                path
                for path in REQUIRED_GOVERNANCE_FILES
                if path.endswith("/role.yaml")
            },
            {
                f"universal_research_mcp/governance/roles/{agent_id}/role.yaml"
                for agent_id in FIXED_ROSTER
            },
        )
        self.assertEqual(
            {
                path
                for path in REQUIRED_GOVERNANCE_FILES
                if path.endswith("/instructions.md")
            },
            {
                f"universal_research_mcp/governance/roles/{agent_id}/instructions.md"
                for agent_id in FIXED_ROSTER
            },
        )
        self.assertIn(
            "universal_research_mcp/governance/schemas/prompt-pack.schema.json",
            REQUIRED_GOVERNANCE_FILES,
        )

        omitted = "universal_research_mcp/governance/roles/retrieval_governor/instructions.md"
        wheel_members = {
            *REQUIRED_RUNTIME_FILES,
            *REQUIRED_GOVERNANCE_FILES,
            *(BUNDLE_PREFIX + name for name in REQUIRED_BUNDLE_FILES),
            "universal_research_mcp-0.4.0.dist-info/entry_points.txt",
        }
        wheel_members.remove(omitted)
        with tempfile.TemporaryDirectory() as temporary_directory:
            wheel = Path(temporary_directory) / "fixture.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for member in sorted(wheel_members):
                    archive.writestr(member, "")
            self.assertEqual(validate_wheel(wheel), [omitted])

    def test_plugin_has_no_repository_relative_launcher_path(self) -> None:
        config = json.loads(
            (ROOT / "plugin/universal-research-memory/.mcp.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            list(config["mcpServers"]),
            ["universal_research"],
        )
        server = config["mcpServers"]["universal_research"]
        self.assertEqual(server["command"], "universal-research")
        self.assertEqual(server["args"], ["serve", "--auto-index"])
        self.assertNotIn("env", server)
        manifest = json.loads(
            (
                ROOT / "plugin/universal-research-memory/.codex-plugin/plugin.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(manifest["version"].startswith("0.4.0+codex."))
        self.assertEqual(manifest["author"]["name"], "mp-juns")
        self.assertEqual(manifest["interface"]["developerName"], "mp-juns")
        self.assertIn("Codex-only", manifest["description"])
        self.assertIn("Codex host", manifest["interface"]["longDescription"])
        self.assertIn("external model APIs", manifest["interface"]["longDescription"])
        self.assertIn("Codex host-owned", manifest["interface"]["defaultPrompt"])
        self.assertIn("visualization off", manifest["interface"]["defaultPrompt"])


if __name__ == "__main__":
    unittest.main()
