import asyncio
import os
from pathlib import Path
import hashlib
import sqlite3
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

    def test_default_tool_surface_is_lexical_and_hides_legacy_aliases(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        self.assertNotIn("research_search", tools)
        self.assertNotIn("research_provider_status", tools)
        self.assertFalse(any(name.startswith("agent_runtime_") for name in tools))
        mode_schema = tools["memory_search_candidates"].inputSchema["properties"]["mode"]
        self.assertEqual(mode_schema["const"], "lexical")
        with self.assertRaisesRegex(ValueError, "not exposed"):
            server.memory_search_candidates("query", mode="semantic")

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

    def test_fetch_is_limited_to_registered_source_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "docs/evidence.md"
            unregistered = root / "docs/personal.txt"
            source.parent.mkdir(parents=True)
            source.write_text("registered evidence\n", encoding="utf-8")
            unregistered.write_text("not indexed\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            database = root / "data/index/research.sqlite"
            database.parent.mkdir(parents=True)
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE events (event_id TEXT, source_path TEXT, source_sha256 TEXT)")
                connection.execute("CREATE TABLE sources (source_path TEXT, source_sha256 TEXT)")
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?)",
                    ("event_1", "docs/evidence.md", digest),
                )
            server.configure_runtime(root, database)

            evidence = server.memory_fetch_evidence(
                "docs/evidence.md", 1, 1, 0, "event_1", digest,
            )
            self.assertEqual(evidence["integrity_status"], "matched")
            self.assertEqual(evidence["expected_sha256"], digest)
            with self.assertRaisesRegex(ValueError, "not registered"):
                server.memory_fetch_evidence("docs/personal.txt", 1, 1, 0)

    def test_fetch_requires_revision_when_one_path_has_multiple_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "docs/evidence.md"
            source.parent.mkdir(parents=True)
            source.write_text("current\n", encoding="utf-8")
            current = hashlib.sha256(source.read_bytes()).hexdigest()
            database = root / "data/index/research.sqlite"
            database.parent.mkdir(parents=True)
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE events (event_id TEXT, source_path TEXT, source_sha256 TEXT)")
                connection.execute("CREATE TABLE sources (source_path TEXT, source_sha256 TEXT)")
                connection.executemany(
                    "INSERT INTO events VALUES (?, ?, ?)",
                    [
                        ("event_old", "docs/evidence.md", "0" * 64),
                        ("event_current", "docs/evidence.md", current),
                    ],
                )
            server.configure_runtime(root, database)

            with self.assertRaisesRegex(ValueError, "multiple indexed revisions"):
                server.memory_fetch_evidence("docs/evidence.md", 1, 1, 0)
            evidence = server.memory_fetch_evidence(
                "docs/evidence.md", 1, 1, 0, "event_current", current,
            )
            self.assertEqual(evidence["integrity_status"], "matched")


if __name__ == "__main__":
    unittest.main()
