from pathlib import Path
import tomllib
import unittest

from universal_research_mcp import __version__
from scripts.require_urmcp_release_tag import expected_tag


ROOT = Path(__file__).resolve().parents[1]
ALIAS_ROOT = ROOT / "packages" / "urmcp"


class UrmcpAliasDistributionTests(unittest.TestCase):
    def test_alias_metadata_tracks_the_core_release_exactly(self) -> None:
        with (ALIAS_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(project["name"], "urmcp")
        self.assertEqual(project["version"], __version__)
        self.assertEqual(
            project["dependencies"],
            [f"universal-research-mcp=={__version__}"],
        )
        self.assertEqual(project["scripts"]["urmcp"], "universal_research_mcp.cli:main")

    def test_alias_is_documented_as_a_thin_install_shim(self) -> None:
        readme = (ALIAS_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("contains no second MCP implementation", readme)
        self.assertIn("pip install --upgrade urmcp", readme)
        self.assertIn("universal-research-mcp", readme)

    def test_alias_release_tag_tracks_the_core_release(self) -> None:
        self.assertEqual(expected_tag(), f"urmcp-v{__version__}")


if __name__ == "__main__":
    unittest.main()
