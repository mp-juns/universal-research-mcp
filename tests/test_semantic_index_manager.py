from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from universal_research_mcp.core.index_refresh import validate_index_health_record
from jsonschema import Draft202012Validator
from universal_research_mcp.indexing import ensure_lexical_index, initialize_project
from universal_research_mcp.indexing.semantic import (
    ensure_semantic_index,
    normalize_vector,
    semantic_status,
)


class FakeEmbedder:
    def __init__(self, *, width: int = 3, error: Exception | None = None) -> None:
        self.width = width
        self.error = error
        self.calls: list[dict] = []

    def embed(self, texts, *, model, dimensions):
        self.calls.append(
            {"texts": tuple(texts), "model": model, "dimensions": dimensions}
        )
        if self.error is not None:
            raise self.error
        width = dimensions or self.width
        return tuple(
            tuple(float(index + offset + 1) for offset in range(width))
            for index, _text in enumerate(texts)
        )


class NeverEmbedder:
    def embed(self, texts, *, model, dimensions):  # pragma: no cover - failure guard
        raise AssertionError("empty ledger must not invoke the embedder")


def _write_fixture(root: Path, *, summary: str = "bounded research summary") -> Path:
    evidence = root / "docs/evidence.md"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        "outside range secret\n"
        "approved evidence alpha\n"
        "approved evidence beta\n"
        "another outside secret\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    events_root = root / "data/events"
    events_root.mkdir(parents=True, exist_ok=True)
    (events_root / "sources.jsonl").write_text(
        json.dumps(
            {
                "source_id": "src_evidence",
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
                "event_id": "evt_semantic",
                "date": "2026-08-04",
                "event_type": "observation",
                "status": "completed",
                "project": "fixture",
                "workstream": "semantic",
                "summary": summary,
                "source": {
                    "source_path": "docs/evidence.md",
                    "heading": "Approved result",
                    "source_sha256": digest,
                    "line_start": 2,
                    "line_end": 3,
                    "legacy_import": False,
                    "requires_human_review": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ensure_lexical_index(root)
    return evidence


class SemanticIndexManagerTests(unittest.TestCase):
    def assert_valid_health(self, root: Path, health: dict) -> None:
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "schemas/index-health.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(health)
        self.assertEqual(validate_index_health_record(health), [])

    def test_empty_current_ledger_builds_without_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_project(root)

            report = ensure_semantic_index(
                root,
                NeverEmbedder(),
                provider_id="local-test",
                model="empty-model",
            )

            self.assertTrue(report["executed"])
            self.assertEqual(report["verification"]["embedding_count"], 0)
            self.assertEqual(report["verification"]["passage_count"], 0)
            self.assertEqual(report["dimensions"], 0)
            health = json.loads(
                (root / "data/index/semantic-health.json").read_text(encoding="utf-8")
            )
            self.assert_valid_health(root, health)
            self.assertEqual(health["status"], "succeeded")
            self.assertEqual(health["retrieval_verification"]["mode"], "empty_index")
            self.assertEqual(
                semantic_status(
                    root, provider_id="local-test", model="empty-model"
                )["status"],
                "current",
            )

    def test_build_uses_only_allowlisted_fields_and_verified_source_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _write_fixture(root)
            lexical_db = root / "data/index/research.sqlite"
            with closing(sqlite3.connect(lexical_db)) as connection:
                connection.execute(
                    "UPDATE events SET raw_json = ? WHERE event_id = ?",
                    ('{"raw_prompt":"RAW_PROMPT_MUST_NOT_LEAK"}', "evt_semantic"),
                )
                connection.commit()
            embedder = FakeEmbedder(width=3)

            report = ensure_semantic_index(
                root,
                embedder,
                provider_id="fake-local",
                model="fake-v1",
                dimensions=3,
            )

            sent = "\n".join(
                text for call in embedder.calls for text in call["texts"]
            )
            self.assertIn("bounded research summary", sent)
            self.assertIn("approved evidence alpha", sent)
            self.assertIn("approved evidence beta", sent)
            self.assertNotIn("outside range secret", sent)
            self.assertNotIn("another outside secret", sent)
            self.assertNotIn("RAW_PROMPT_MUST_NOT_LEAK", sent)
            self.assertEqual(report["verification"]["embedding_count"], 1)
            self.assertEqual(report["verification"]["passage_count"], 1)

            semantic_db = root / "data/index/semantic.sqlite"
            health = json.loads(
                (root / "data/index/semantic-health.json").read_text(encoding="utf-8")
            )
            self.assert_valid_health(root, health)
            self.assertEqual(health["status"], "succeeded")
            self.assertEqual(health["index_revision"], report["indexed_fingerprint"])
            self.assertEqual(health["event_count"], 1)
            self.assertEqual(health["passage_count"], 1)
            self.assertEqual(health["embedding_model"], "fake-local:fake-v1")
            self.assertEqual(health["embedding_dimension"], 3)
            self.assertEqual(health["source_event_ids"], ["evt_semantic"])
            self.assertEqual(health["retrieval_verification"]["status"], "passed")
            self.assertTrue(
                health["retrieval_verification"]["canonical_event_fetched"]
            )
            self.assertTrue(
                health["retrieval_verification"]["canonical_source_fetched"]
            )
            self.assertTrue(health["retrieval_verification"]["source_slice_fetched"])
            self.assertEqual(
                health["artifact_hashes"],
                [
                    {
                        "path": "data/index/semantic.sqlite",
                        "sha256": hashlib.sha256(semantic_db.read_bytes()).hexdigest(),
                    }
                ],
            )
            with closing(sqlite3.connect(semantic_db)) as connection:
                dimensions, blob = connection.execute(
                    "SELECT dimensions, vector FROM embeddings"
                ).fetchone()
                passage = connection.execute(
                    "SELECT source_path, line_start, line_end FROM passage_embeddings"
                ).fetchone()
            values = struct.unpack(f"<{dimensions}f", blob)
            self.assertTrue(math.isclose(math.sqrt(sum(v * v for v in values)), 1.0, rel_tol=1e-5))
            self.assertEqual(passage, ("docs/evidence.md", 2, 3))
            self.assertEqual(
                semantic_status(
                    root,
                    provider_id="fake-local",
                    model="fake-v1",
                    dimensions=3,
                )["status"],
                "current",
            )

            evidence.write_text("changed after indexing\n", encoding="utf-8")
            self.assertEqual(semantic_status(root)["status"], "stale")

    def test_model_provider_and_dimension_are_part_of_currentness_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            ensure_semantic_index(
                root,
                FakeEmbedder(),
                provider_id="approved-provider",
                model="approved-model",
                dimensions=3,
            )

            self.assertEqual(
                semantic_status(
                    root,
                    provider_id="other-provider",
                    model="approved-model",
                    dimensions=3,
                )["failed_checks"],
                ["provider_id"],
            )
            self.assertEqual(
                semantic_status(
                    root,
                    provider_id="approved-provider",
                    model="other-model",
                    dimensions=3,
                )["status"],
                "stale",
            )
            self.assertEqual(
                semantic_status(
                    root,
                    provider_id="approved-provider",
                    model="approved-model",
                    dimensions=2,
                )["status"],
                "stale",
            )

    def test_failed_health_marks_search_stale_and_repairs_without_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            ensure_semantic_index(
                root,
                FakeEmbedder(),
                provider_id="fake-local",
                model="fake-v1",
                dimensions=3,
            )
            health_path = root / "data/index/semantic-health.json"
            health = json.loads(health_path.read_text(encoding="utf-8"))
            health["status"] = "stale"
            health["retrieval_verification"] = {
                "status": "failed",
                "previous_good_database_preserved": True,
            }
            health["failures"] = [
                {"failure_type": "RuntimeError", "message": "verification interrupted"}
            ]
            health_path.write_text(json.dumps(health) + "\n", encoding="utf-8")

            stale = semantic_status(
                root,
                provider_id="fake-local",
                model="fake-v1",
                dimensions=3,
            )
            self.assertEqual(stale["status"], "stale")
            self.assertTrue(stale["structural_current"])
            self.assertIn("health_record", stale["failed_checks"])

            repaired = ensure_semantic_index(
                root,
                NeverEmbedder(),
                provider_id="fake-local",
                model="fake-v1",
                dimensions=3,
            )
            self.assertEqual(repaired["decision"], "health_repaired")
            self.assertFalse(repaired["executed"])
            self.assertEqual(repaired["status"], "current")
            repaired_health = json.loads(health_path.read_text(encoding="utf-8"))
            self.assert_valid_health(root, repaired_health)
            self.assertEqual(repaired_health["status"], "succeeded")

    def test_failed_refresh_has_no_retry_and_preserves_previous_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            ensure_semantic_index(
                root,
                FakeEmbedder(),
                provider_id="fake-local",
                model="fake-v1",
                dimensions=3,
            )
            database = root / "data/index/semantic.sqlite"
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            with closing(
                sqlite3.connect(root / "data/index/research.sqlite")
            ) as connection:
                connection.execute(
                    "UPDATE events SET summary = ? WHERE event_id = ?",
                    ("changed semantic input", "evt_semantic"),
                )
                connection.commit()
            secret = "sk-semantic-health-secret-value"
            failing = FakeEmbedder(
                error=RuntimeError(f"Authorization: Bearer {secret}")
            )

            with self.assertRaises(RuntimeError):
                ensure_semantic_index(
                    root,
                    failing,
                    provider_id="fake-local",
                    model="fake-v1",
                    dimensions=3,
                )

            self.assertEqual(len(failing.calls), 1)
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
            self.assertEqual(
                list((root / "data/index").glob(".semantic.sqlite.*.staging")), []
            )
            health_path = root / "data/index/semantic-health.json"
            health = json.loads(health_path.read_text(encoding="utf-8"))
            self.assert_valid_health(root, health)
            self.assertEqual(health["status"], "stale")
            self.assertEqual(health["retrieval_verification"]["status"], "failed")
            self.assertTrue(
                health["retrieval_verification"]["previous_good_database_preserved"]
            )
            self.assertNotIn(secret, json.dumps(health, sort_keys=True))
            self.assertIn("[REDACTED]", health["failures"][0]["message"])
            self.assertEqual(
                list((root / "data/index").glob(".semantic-health.json.*.staging")),
                [],
            )

    def test_first_build_failure_records_failed_health_without_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            failing = FakeEmbedder(error=RuntimeError("encoder unavailable"))

            with self.assertRaisesRegex(RuntimeError, "encoder unavailable"):
                ensure_semantic_index(
                    root,
                    failing,
                    provider_id="fake-local",
                    model="fake-v1",
                    dimensions=3,
                )

            health = json.loads(
                (root / "data/index/semantic-health.json").read_text(encoding="utf-8")
            )
            self.assert_valid_health(root, health)
            self.assertEqual(health["status"], "failed")
            self.assertEqual(health["embedding_dimension"], 3)
            self.assertEqual(health["artifact_hashes"], [])
            self.assertEqual(health["source_event_ids"], [])
            self.assertFalse(
                health["retrieval_verification"]["previous_good_database_preserved"]
            )

    def test_non_finite_zero_and_wrong_dimensions_are_rejected(self) -> None:
        for vector, pattern in (
            ([float("nan"), 1.0], "non-finite"),
            ([0.0, 0.0], "zero norm"),
            ([1.0, 2.0, 3.0], "dimension mismatch"),
        ):
            with self.subTest(vector=vector):
                with self.assertRaisesRegex(ValueError, pattern):
                    normalize_vector(vector, dimensions=2)


if __name__ == "__main__":
    unittest.main()
