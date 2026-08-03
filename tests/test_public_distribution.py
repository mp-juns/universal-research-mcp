import json
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicDistributionTests(unittest.TestCase):
    def test_package_exposes_stable_mcp_entry_point(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        self.assertEqual(project["project"]["name"], "universal-research-mcp")
        self.assertEqual(
            project["project"]["scripts"]["universal-research-mcp"],
            "universal_research_mcp.server:main",
        )
        self.assertIn("MIT License", (ROOT / "LICENSE").read_text(encoding="utf-8"))

    def test_plugin_has_no_repository_relative_launcher_path(self) -> None:
        config = json.loads(
            (ROOT / "plugin/universal-research-memory/.mcp.json").read_text(encoding="utf-8")
        )
        server = config["mcpServers"]["universal_research_memory"]
        self.assertEqual(server["command"], "universal-research-mcp")
        self.assertEqual(server["args"], [])
        self.assertNotIn("env", server)


if __name__ == "__main__":
    unittest.main()
