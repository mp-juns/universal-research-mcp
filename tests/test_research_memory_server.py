import os
from pathlib import Path
import tempfile
import unittest

from universal_research_mcp import server


class ResearchMemoryServerSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prior_root = server.ROOT
        self.prior_db = server.RESEARCH_DB
        self.prior_events = server.EVENTS_ROOT

    def tearDown(self) -> None:
        server.configure_runtime(self.prior_root, self.prior_db, self.prior_events)

    def test_cli_parser_accepts_explicit_project_paths(self) -> None:
        parsed = server.parse_args([
            "--root", "fixture-root", "--lexical-db", "fixture.sqlite",
            "--events-root", "fixture-events",
        ])
        self.assertEqual(parsed.root, Path("fixture-root"))
        self.assertEqual(parsed.lexical_db, Path("fixture.sqlite"))
        self.assertEqual(parsed.events_root, Path("fixture-events"))

    def test_safe_path_rejects_sensitive_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs" / "evidence.md").write_text("safe\n", encoding="utf-8")
            os.symlink("/etc/hosts", root / "docs" / "outside.md")
            server.configure_runtime(root)

            self.assertEqual(server.resolve_safe_path("docs/evidence.md"), root / "docs" / "evidence.md")
            with self.assertRaisesRegex(ValueError, "denied"):
                server.resolve_safe_path(".env")
            with self.assertRaisesRegex(ValueError, "escapes"):
                server.resolve_safe_path("docs/outside.md")


if __name__ == "__main__":
    unittest.main()
