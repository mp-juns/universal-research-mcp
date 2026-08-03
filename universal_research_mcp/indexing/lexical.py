"""Atomic lifecycle for the derived lexical research index.

Only ``initialize_project`` may create the empty canonical source manifest;
all populated canonical JSONL stays read-only. Callers remain responsible for
governance approval before invoking a mutating function.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Callable

from universal_research_mcp.runtime import ProjectPaths


FINGERPRINT_VERSION = "canonical-jsonl-sha256-v1"
FINGERPRINT_KEY = "canonical_bundle_sha256"
REQUIRED_TABLES = frozenset(
    {"metadata", "sources", "events", "relations", "artifacts", "event_fts"}
)
LexicalBuilder = Callable[[Path, Path], dict[str, Any]]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: source record must be an object")
        records.append(record)
    return records


def validate_registered_sources(
    project_root: Path,
    sources_path: Path,
) -> dict[str, Any]:
    """Verify only explicitly registered canonical source artifacts.

    This is deliberately an allowlist walk: no directory is scanned while
    validating canonical evidence.
    """

    root = project_root.resolve()
    if not sources_path.is_file():
        raise FileNotFoundError(f"canonical source manifest is missing: {sources_path}")
    records = _read_jsonl(sources_path)
    if not records:
        raise ValueError("populated canonical ledger has no registered sources")

    verified: list[dict[str, str]] = []
    for index, record in enumerate(records, start=1):
        path_text = record.get("source_path")
        expected = record.get("source_sha256")
        if not isinstance(path_text, str) or not path_text.strip():
            raise ValueError(f"source record {index} has no source_path")
        supplied = Path(path_text)
        if supplied.is_absolute():
            raise ValueError(f"registered source path must be relative: {path_text}")
        candidate = (root / supplied).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"registered source path escapes project root: {path_text}"
            ) from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"registered source does not exist: {path_text}")
        if not isinstance(expected, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", expected
        ):
            raise ValueError(f"registered source has invalid SHA-256: {path_text}")
        actual = _sha256_file(candidate)
        if actual.lower() != expected.lower():
            raise ValueError(f"registered source SHA-256 mismatch: {path_text}")
        verified.append({"path": supplied.as_posix(), "sha256": actual})
    return {"registered_count": len(records), "verified_sources": verified}


def _canonical_paths(events_root: Path) -> list[Path]:
    paths: list[Path] = []
    sources = events_root / "sources.jsonl"
    if sources.is_file():
        paths.append(sources)
    paths.extend(sorted((events_root / "daily").glob("*/events.jsonl")))
    return paths


def canonical_fingerprint(events_root: Path) -> dict[str, Any]:
    """Hash only the canonical source manifest and daily event JSONL files."""

    digest = hashlib.sha256()
    digest.update(FINGERPRINT_VERSION.encode("ascii") + b"\0")
    paths = _canonical_paths(events_root)
    for path in paths:
        relative = path.relative_to(events_root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return {
        "algorithm": FINGERPRINT_VERSION,
        "sha256": digest.hexdigest(),
        "file_count": len(paths),
        "files": [path.relative_to(events_root).as_posix() for path in paths],
    }


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = DELETE;
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sources (
            source_id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL UNIQUE,
            source_sha256 TEXT NOT NULL,
            source_type TEXT NOT NULL,
            legacy_import INTEGER NOT NULL
        );
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            project TEXT NOT NULL,
            workstream TEXT,
            summary TEXT NOT NULL,
            source_path TEXT,
            source_heading TEXT,
            source_sha256 TEXT,
            line_start INTEGER,
            line_end INTEGER,
            legacy_import INTEGER NOT NULL,
            requires_human_review INTEGER NOT NULL,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE relations (
            event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL,
            target TEXT NOT NULL,
            PRIMARY KEY (event_id, relation_type, target)
        );
        CREATE TABLE artifacts (
            event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            sha256 TEXT,
            role TEXT,
            PRIMARY KEY (event_id, path)
        );
        CREATE INDEX events_date_idx ON events(date);
        CREATE INDEX events_status_idx ON events(status);
        CREATE INDEX events_type_idx ON events(event_type);
        CREATE INDEX relations_target_idx ON relations(target);
        CREATE VIRTUAL TABLE event_fts USING fts5(
            event_id UNINDEXED,
            summary,
            source_heading,
            source_path,
            tokenize = 'unicode61'
        );
        """
    )


def _build_empty(output: Path) -> dict[str, Any]:
    with sqlite3.connect(output) as connection:
        _initialize_schema(connection)
        metadata = {
            "schema_version": "1.0",
            "index_kind": "sqlite_fts5_bm25_relation_index",
            "source_count": "0",
            "event_count": "0",
            "reference_count": "0",
            "record_count": "0",
        }
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        connection.commit()
    return {"output": str(output), **metadata}


def _existing_builder(
    project_root: Path,
    events_root: Path,
    output: Path,
) -> dict[str, Any]:
    # Imported lazily so fresh empty bootstrap has no optional document/runtime
    # dependency and populated ledgers retain the existing compatibility rules.
    from scripts.build_research_ledger_index import build

    return build(events_root, output, project_root=project_root)


def _record_fingerprint(database: Path, fingerprint: dict[str, Any]) -> None:
    metadata = {
        FINGERPRINT_KEY: fingerprint["sha256"],
        "canonical_fingerprint_algorithm": fingerprint["algorithm"],
        "canonical_file_count": str(fingerprint["file_count"]),
        "index_built_at": datetime.now(timezone.utc).isoformat(),
    }
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO metadata VALUES (?, ?)", metadata.items()
        )
        connection.commit()


def verify_lexical_index(database: Path, expected_fingerprint: str) -> dict[str, Any]:
    """Verify integrity, FTS retrieval, and canonical evidence eligibility."""

    if not database.is_file():
        raise RuntimeError(f"staged lexical index was not created: {database}")
    with closing(
        sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    ) as db:
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise RuntimeError(f"lexical index integrity check failed: {integrity}")
        tables = {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise RuntimeError(f"lexical index is missing required tables: {missing}")
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
        if metadata.get(FINGERPRINT_KEY) != expected_fingerprint:
            raise RuntimeError("lexical index canonical fingerprint mismatch")
        event_count = int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        fts_count = int(db.execute("SELECT COUNT(*) FROM event_fts").fetchone()[0])
        if event_count != fts_count:
            raise RuntimeError(
                f"lexical event/FTS row count mismatch: {event_count} != {fts_count}"
            )
        event_rows = list(
            db.execute("SELECT event_id, summary FROM events ORDER BY rowid")
        )
        retrieved_event_id: str | None = None
        if event_rows:
            first_event_id, first_summary = event_rows[0]
            token_match = re.search(r"\w+", str(first_summary), flags=re.UNICODE)
            if token_match is None:
                raise RuntimeError("indexed events contain no searchable summary token")
            query = '"' + token_match.group(0).replace('"', '""') + '"'
            retrieved = db.execute(
                "SELECT event_id FROM event_fts WHERE event_fts MATCH ? LIMIT 1",
                (query,),
            ).fetchone()
            if retrieved is None:
                raise RuntimeError(
                    "lexical FTS retrieval verification returned no event"
                )
            retrieved_event_id = str(retrieved[0])

        canonical_count = int(metadata.get("event_count", event_count))
        eligible_count = int(
            db.execute(
                """
                SELECT COUNT(*)
                FROM events AS e
                JOIN sources AS s
                  ON s.source_path = e.source_path
                 AND lower(s.source_sha256) = lower(e.source_sha256)
                WHERE e.event_type <> 'reference_document'
                """
            ).fetchone()[0]
        )
        ineligible_source_count = int(
            db.execute(
                """
                SELECT COUNT(*)
                FROM events AS e
                LEFT JOIN sources AS s
                  ON s.source_path = e.source_path
                 AND lower(s.source_sha256) = lower(e.source_sha256)
                WHERE e.event_type <> 'reference_document'
                  AND e.source_path IS NOT NULL
                  AND e.source_path <> ''
                  AND s.source_id IS NULL
                """
            ).fetchone()[0]
        )
        if ineligible_source_count:
            raise RuntimeError(
                "canonical events reference unregistered or hash-ineligible evidence"
            )
        if canonical_count > 0 and eligible_count == 0:
            raise RuntimeError(
                "populated canonical ledger has no source-evidence-eligible event"
            )
    return {
        "integrity": integrity,
        "event_count": event_count,
        "fts_count": fts_count,
        "fingerprint": expected_fingerprint,
        "event_ids": [str(row[0]) for row in event_rows],
        "retrieved_event_id": retrieved_event_id,
        "source_evidence_eligible_count": eligible_count,
        "ineligible_source_count": ineligible_source_count,
    }


def index_status(root: str | Path) -> dict[str, Any]:
    paths = ProjectPaths.from_root(root)
    fingerprint = canonical_fingerprint(paths.events_root)
    result: dict[str, Any] = {
        "lexical_db": str(paths.lexical_db),
        "index_health": str(paths.index_health),
        "current_fingerprint": fingerprint["sha256"],
        "canonical_file_count": fingerprint["file_count"],
    }
    health: dict[str, Any] | None = None
    if paths.index_health.is_file():
        try:
            loaded = json.loads(paths.index_health.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                health = loaded
                result["health_status"] = loaded.get("status")
        except (OSError, json.JSONDecodeError) as exc:
            result["health_status"] = "failed"
            result["health_reason"] = f"{type(exc).__name__}: {exc}"
    if not paths.lexical_db.is_file():
        return {
            **result,
            "status": "missing",
            "stale": True,
            "indexed_fingerprint": None,
        }
    try:
        with closing(
            sqlite3.connect(f"file:{paths.lexical_db.as_posix()}?mode=ro", uri=True)
        ) as db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {
            **result,
            "status": "corrupt",
            "stale": True,
            "indexed_fingerprint": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    indexed = metadata.get(FINGERPRINT_KEY)
    failed_current_health = bool(
        health
        and health.get("index_revision") == fingerprint["sha256"]
        and health.get("status") in {"partial", "failed", "stale"}
    )
    current = (
        integrity == "ok"
        and indexed == fingerprint["sha256"]
        and not failed_current_health
    )
    return {
        **result,
        "status": "current" if current else "stale",
        "stale": not current,
        "indexed_fingerprint": indexed,
        "integrity": integrity,
    }


def _sync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _database_snapshot(database: Path, project_root: Path) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "event_count": 0,
        "passage_count": 0,
        "source_event_ids": [],
        "artifact_hashes": [],
    }
    if not database.is_file():
        return snapshot
    try:
        snapshot["artifact_hashes"] = [
            {
                "path": database.relative_to(project_root).as_posix(),
                "sha256": _sha256_file(database),
            }
        ]
        with closing(
            sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        ) as db:
            snapshot["event_count"] = int(
                db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            )
            snapshot["passage_count"] = int(
                db.execute("SELECT COUNT(*) FROM event_fts").fetchone()[0]
            )
            snapshot["source_event_ids"] = [
                str(row[0])
                for row in db.execute("SELECT event_id FROM events ORDER BY rowid")
            ]
    except (OSError, sqlite3.Error, ValueError):
        # A failed/corrupt index has no claimable derived contents. Its file hash
        # remains useful when it could be computed before SQLite inspection.
        snapshot["event_count"] = 0
        snapshot["passage_count"] = 0
        snapshot["source_event_ids"] = []
    return snapshot


def _atomic_write_health(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _success_health(
    paths: ProjectPaths,
    fingerprint: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    snapshot = _database_snapshot(paths.lexical_db, paths.root)
    return {
        "schema_version": "research-index-health-v1",
        "status": "succeeded",
        "index_revision": fingerprint["sha256"],
        "event_count": snapshot["event_count"],
        "passage_count": snapshot["passage_count"],
        "embedding_model": "lexical-fts5",
        "embedding_dimension": 0,
        "artifact_hashes": snapshot["artifact_hashes"],
        "source_event_ids": snapshot["source_event_ids"],
        "retrieval_verification": {
            "status": "passed",
            "event_fts_parity": (
                verification["event_count"] == verification["fts_count"]
            ),
            "retrieved_event_id": verification["retrieved_event_id"],
            "source_evidence_eligible_count": verification[
                "source_evidence_eligible_count"
            ],
        },
        "failures": [],
    }


def _failure_health(
    paths: ProjectPaths,
    fingerprint: dict[str, Any] | None,
    before: dict[str, Any] | None,
    error: Exception,
) -> dict[str, Any]:
    snapshot = _database_snapshot(paths.lexical_db, paths.root)
    previous_usable = bool(
        paths.lexical_db.is_file()
        and before
        and before.get("status") in {"current", "stale"}
        and before.get("integrity") == "ok"
    )
    return {
        "schema_version": "research-index-health-v1",
        "status": "stale" if previous_usable else "failed",
        "index_revision": (fingerprint["sha256"] if fingerprint else "unavailable"),
        "event_count": snapshot["event_count"],
        "passage_count": snapshot["passage_count"],
        "embedding_model": "lexical-fts5",
        "embedding_dimension": 0,
        "artifact_hashes": snapshot["artifact_hashes"],
        "source_event_ids": snapshot["source_event_ids"],
        "retrieval_verification": {
            "status": "failed",
            "previous_good_database_preserved": previous_usable,
        },
        "failures": [
            {
                "failure_type": type(error).__name__,
                "message": str(error),
            }
        ],
    }


def ensure_lexical_index(
    root: str | Path,
    *,
    builder: LexicalBuilder | None = None,
) -> dict[str, Any]:
    """Create or refresh the index without exposing a partial database."""

    paths = ProjectPaths.from_root(root)
    paths.events_root.mkdir(parents=True, exist_ok=True)
    paths.index_root.mkdir(parents=True, exist_ok=True)
    before: dict[str, Any] | None = None
    fingerprint: dict[str, Any] | None = None
    staging: Path | None = None
    try:
        before = index_status(paths.root)
        fingerprint = canonical_fingerprint(paths.events_root)
        daily_events = sorted((paths.events_root / "daily").glob("*/events.jsonl"))
        if daily_events:
            validate_registered_sources(paths.root, paths.events_root / "sources.jsonl")

        if before["status"] == "current":
            verification = verify_lexical_index(paths.lexical_db, fingerprint["sha256"])
            health = _success_health(paths, fingerprint, verification)
            _atomic_write_health(paths.index_health, health)
            return {
                **before,
                "executed": False,
                "decision": "already_current",
                "verification": verification,
                "health": health,
            }

        descriptor, staging_name = tempfile.mkstemp(
            prefix=f".{paths.lexical_db.name}.",
            suffix=".staging",
            dir=paths.index_root,
        )
        os.close(descriptor)
        staging = Path(staging_name)
        if daily_events:
            if builder is None:
                _existing_builder(paths.root, paths.events_root, staging)
            else:
                builder(paths.events_root, staging)
        else:
            _build_empty(staging)
        fingerprint_after_build = canonical_fingerprint(paths.events_root)
        if fingerprint_after_build["sha256"] != fingerprint["sha256"]:
            raise RuntimeError("canonical JSONL changed during lexical index build")
        _record_fingerprint(staging, fingerprint)
        verification = verify_lexical_index(staging, fingerprint["sha256"])
        _sync_file(staging)
        os.replace(staging, paths.lexical_db)
        health = _success_health(paths, fingerprint, verification)
        _atomic_write_health(paths.index_health, health)
        return {
            "lexical_db": str(paths.lexical_db),
            "index_health": str(paths.index_health),
            "status": "current",
            "stale": False,
            "executed": True,
            "decision": (
                "bootstrapped" if before["status"] == "missing" else "refreshed"
            ),
            "current_fingerprint": fingerprint["sha256"],
            "indexed_fingerprint": fingerprint["sha256"],
            "canonical_file_count": fingerprint["file_count"],
            "verification": verification,
            "health": health,
        }
    except Exception as exc:
        health = _failure_health(paths, fingerprint, before, exc)
        _atomic_write_health(paths.index_health, health)
        raise
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def initialize_project(root: str | Path) -> dict[str, Any]:
    """Create project roots, an empty canonical manifest, and the derived index."""

    paths = ProjectPaths.from_root(root)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.events_root.mkdir(parents=True, exist_ok=True)
    (paths.events_root / "sources.jsonl").touch(exist_ok=True)
    return ensure_lexical_index(paths.root)
