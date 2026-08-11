from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from universal_research_mcp.core.index_refresh import validate_index_health_record
from jsonschema import Draft202012Validator
import universal_research_mcp.tools.build_research_ledger_index as ledger_builder
from universal_research_mcp.indexing.lexical import (
    ensure_lexical_index,
    index_status,
    initialize_project,
)
from universal_research_mcp.runtime import ProjectPaths


def write_populated_fixture(root: Path) -> tuple[Path, Path, str]:
    source = root / "docs/evidence.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# Evidence\n\nVerified fixture evidence.\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    events_root = root / "data/events"
    events_root.mkdir(parents=True, exist_ok=True)
    (events_root / "sources.jsonl").write_text(
        json.dumps(
            {
                "source_id": "src_fixture",
                "source_path": "docs/evidence.md",
                "source_sha256": digest,
                "source_type": "markdown",
                "legacy_import": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    daily = events_root / "daily/2026-08-04/events.jsonl"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "event_id": "evt_fixture",
                "date": "2026-08-04",
                "event_type": "observation",
                "status": "completed",
                "project": "fixture",
                "summary": "automatic lexical fixture",
                "source": {
                    "source_path": "docs/evidence.md",
                    "source_sha256": digest,
                    "heading": "Evidence",
                    "line_start": 1,
                    "line_end": 3,
                    "legacy_import": False,
                    "requires_human_review": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return events_root, daily, digest


class ProjectPathTests(unittest.TestCase):
    def test_paths_are_root_bound_and_reject_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            self.assertEqual(paths.events_root, root / "data/events")
            self.assertEqual(paths.lexical_db, root / "data/index/research.sqlite")
            with self.assertRaisesRegex(ValueError, "relative"):
                paths.resolve_relative(root / "outside")
            with self.assertRaisesRegex(ValueError, "escapes"):
                paths.resolve_relative("../outside")


class LexicalIndexFoundationTests(unittest.TestCase):
    def test_fresh_project_bootstraps_empty_current_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = initialize_project(root)
            self.assertTrue(report["executed"])
            self.assertEqual(report["decision"], "bootstrapped")
            self.assertEqual(report["verification"]["event_count"], 0)
            manifest = root / "data/events/sources.jsonl"
            self.assertTrue(manifest.is_file())
            self.assertEqual(manifest.read_text(encoding="utf-8"), "")
            self.assertEqual(index_status(root)["status"], "current")
            health_path = root / "data/index/index-health.json"
            health = json.loads(health_path.read_text(encoding="utf-8"))
            health_schema = json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "schemas/index-health.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(health_schema).validate(health)
            self.assertEqual(validate_index_health_record(health), [])
            self.assertEqual(health["status"], "succeeded")
            self.assertEqual(health["retrieval_verification"]["status"], "passed")
            second = ensure_lexical_index(root)
            self.assertFalse(second["executed"])
            self.assertEqual(second["decision"], "already_current")

    def test_initialize_does_not_overwrite_existing_source_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "data/events/sources.jsonl"
            manifest.parent.mkdir(parents=True)
            original = '{"preserved": true}\n'
            manifest.write_text(original, encoding="utf-8")

            initialize_project(root)

            self.assertEqual(manifest.read_text(encoding="utf-8"), original)

    def test_populated_fixture_uses_compatibility_builder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_populated_fixture(root)

            report = ensure_lexical_index(root)

            self.assertTrue(report["executed"])
            self.assertEqual(index_status(root)["status"], "current")
            with sqlite3.connect(root / "data/index/research.sqlite") as db:
                row = db.execute("SELECT event_id FROM events").fetchone()
            self.assertEqual(row, ("evt_fixture",))
            self.assertEqual(
                report["verification"]["source_evidence_eligible_count"], 1
            )

    def test_packaged_manager_passes_actual_project_root_to_reference_corpus(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_populated_fixture(root)

            with patch.object(
                ledger_builder,
                "build_reference_corpus",
                wraps=ledger_builder.build_reference_corpus,
            ) as reference_builder:
                ensure_lexical_index(root)

            reference_builder.assert_called_once_with(root.resolve())

    def test_canonical_change_marks_index_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_project(root)
            _, daily, _ = write_populated_fixture(root)
            status = index_status(root)
            self.assertEqual(status["status"], "stale")
            self.assertNotEqual(
                status["current_fingerprint"], status["indexed_fingerprint"]
            )

    def test_failed_staging_build_preserves_previous_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_project(root)
            database = root / "data/index/research.sqlite"
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            write_populated_fixture(root)

            def broken_builder(_events_root: Path, output: Path) -> dict:
                output.write_bytes(b"not a sqlite database")
                raise RuntimeError("fixture failure")

            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                ensure_lexical_index(root, builder=broken_builder)

            after = hashlib.sha256(database.read_bytes()).hexdigest()
            self.assertEqual(after, before)
            self.assertEqual(list((root / "data/index").glob("*.staging")), [])
            health = json.loads(
                (root / "data/index/index-health.json").read_text(encoding="utf-8")
            )
            self.assertEqual(validate_index_health_record(health), [])
            self.assertEqual(health["status"], "stale")
            self.assertEqual(health["retrieval_verification"]["status"], "failed")
            self.assertTrue(
                health["retrieval_verification"]["previous_good_database_preserved"]
            )

    def test_invalid_staged_database_is_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_project(root)
            database = root / "data/index/research.sqlite"
            before = database.read_bytes()
            write_populated_fixture(root)

            def incomplete_builder(_events_root: Path, output: Path) -> dict:
                with sqlite3.connect(output) as db:
                    db.execute(
                        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)"
                    )
                return {"output": str(output)}

            with self.assertRaisesRegex(RuntimeError, "missing required tables"):
                ensure_lexical_index(root, builder=incomplete_builder)

            self.assertEqual(database.read_bytes(), before)

    def test_canonical_change_during_build_prevents_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_project(root)
            database = root / "data/index/research.sqlite"
            before = database.read_bytes()
            _, daily, _ = write_populated_fixture(root)

            def changing_builder(_events_root: Path, output: Path) -> dict:
                from universal_research_mcp.tools.build_research_ledger_index import initialize

                with sqlite3.connect(output) as db:
                    initialize(db)
                daily.write_text("{}\n{}\n", encoding="utf-8")
                return {"output": str(output)}

            with self.assertRaisesRegex(RuntimeError, "changed during"):
                ensure_lexical_index(root, builder=changing_builder)

            self.assertEqual(database.read_bytes(), before)

    def test_registered_source_must_exist_inside_root_with_matching_hash(self) -> None:
        cases = (
            ("docs/missing.md", "0" * 64, "does not exist"),
            ("../outside.md", "0" * 64, "escapes project root"),
            ("docs/evidence.md", "0" * 64, "SHA-256 mismatch"),
        )
        for source_path, source_hash, expected_error in cases:
            with self.subTest(source_path=source_path, error=expected_error):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary) / "project"
                    _, daily, _ = write_populated_fixture(root)
                    if source_path == "../outside.md":
                        outside = root.parent / "outside.md"
                        outside.write_text("outside\n", encoding="utf-8")
                        source_hash = hashlib.sha256(outside.read_bytes()).hexdigest()
                    manifest = root / "data/events/sources.jsonl"
                    manifest.write_text(
                        json.dumps(
                            {
                                "source_id": "src_invalid",
                                "source_path": source_path,
                                "source_sha256": source_hash,
                                "source_type": "markdown",
                                "legacy_import": False,
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    self.assertTrue(daily.is_file())

                    with self.assertRaisesRegex(
                        (FileNotFoundError, ValueError), expected_error
                    ):
                        ensure_lexical_index(root)

                    health = json.loads(
                        (root / "data/index/index-health.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(validate_index_health_record(health), [])
                    self.assertEqual(health["status"], "failed")
                    self.assertEqual(
                        health["retrieval_verification"]["status"], "failed"
                    )

    def test_source_mutation_marks_health_stale_and_preserves_good_database(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ensure_lexical_index(root)
            write_populated_fixture(root)
            ensure_lexical_index(root)
            database = root / "data/index/research.sqlite"
            before = database.read_bytes()
            (root / "docs/evidence.md").write_text(
                "# Mutated evidence\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                ensure_lexical_index(root)

            self.assertEqual(database.read_bytes(), before)
            self.assertEqual(index_status(root)["status"], "stale")
            health = json.loads(
                (root / "data/index/index-health.json").read_text(encoding="utf-8")
            )
            self.assertEqual(validate_index_health_record(health), [])
            self.assertEqual(health["status"], "stale")


if __name__ == "__main__":
    unittest.main()
