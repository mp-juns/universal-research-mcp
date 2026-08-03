#!/usr/bin/env python3
"""Build a derived SQLite FTS5 index from the append-only research-event JSONL."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.research_reference_corpus import build_reference_corpus
from scripts.research_event_corrections import apply_source_range_corrections
from core.ledger import validate_records


SCHEMA_VERSION = "1.0"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_artifact(artifact: dict | str) -> dict:
    if isinstance(artifact, str):
        return {"path": artifact}
    return artifact


def normalize_relation(relation: dict | str, *, event_id: str) -> dict:
    """Read the historical event-ID shorthand without rewriting canonical JSONL."""
    if isinstance(relation, str):
        return {"type": "derived_from", "target": relation}
    if isinstance(relation, dict):
        return relation
    raise TypeError(
        f"{event_id}: relation must be an object or legacy event-ID string, "
        f"got {type(relation).__name__}"
    )


def initialize(connection: sqlite3.Connection) -> None:
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


def build(events_root: Path, output: Path) -> dict:
    sources_path = events_root / "sources.jsonl"
    if not sources_path.exists():
        raise FileNotFoundError(f"Missing source manifest: {sources_path}")
    event_paths = sorted((events_root / "daily").glob("*/events.jsonl"))
    if not event_paths:
        raise FileNotFoundError(f"No daily event JSONL under: {events_root / 'daily'}")
    sources = read_jsonl(sources_path)
    canonical_events = [event for path in event_paths for event in read_jsonl(path)]
    validation_issues = validate_records(canonical_events)
    if validation_issues:
        rendered = "; ".join(
            f"{issue.record_id}{issue.path}: {issue.message}"
            for issue in validation_issues
        )
        raise ValueError(f"Canonical ledger validation failed: {rendered}")
    resolved_canonical_events, source_corrections = apply_source_range_corrections(
        canonical_events
    )
    resolved_by_id = {event["event_id"]: event for event in resolved_canonical_events}
    reference_corpus = build_reference_corpus(events_root.parent)
    events = [*canonical_events, *reference_corpus.events]
    event_ids = [event["event_id"] for event in events]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("Duplicate event_id in JSONL input")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with sqlite3.connect(output) as connection:
        initialize(connection)
        connection.executemany(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?)",
            [
                (
                    source["source_id"],
                    source["source_path"],
                    source["source_sha256"],
                    source["source_type"],
                    int(bool(source.get("legacy_import"))),
                )
                for source in sources
            ],
        )
        for event in events:
            effective_event = resolved_by_id.get(event["event_id"], event)
            source = effective_event.get("source", {})
            connection.execute(
                """
                INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["date"],
                    event["event_type"],
                    event["status"],
                    event["project"],
                    event.get("workstream"),
                    event["summary"],
                    source.get("source_path"),
                    source.get("heading"),
                    source.get("source_sha256"),
                    source.get("line_start"),
                    source.get("line_end"),
                    int(bool(source.get("legacy_import"))),
                    int(bool(source.get("requires_human_review"))),
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.execute(
                "INSERT INTO event_fts VALUES (?, ?, ?, ?)",
                (event["event_id"], event["summary"], source.get("heading", ""), source.get("source_path", "")),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO relations VALUES (?, ?, ?)",
                [
                    (
                        event["event_id"],
                        normalized.get("type", "unknown"),
                        normalized.get("target", "unknown"),
                    )
                    for normalized in (
                        normalize_relation(relation, event_id=event["event_id"])
                        for relation in event.get("relations", [])
                    )
                ],
            )
            connection.executemany(
                "INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?)",
                [
                    (
                        event["event_id"],
                        normalized["path"],
                        normalized.get("sha256"),
                        normalized.get("role"),
                    )
                    for normalized in (normalize_artifact(artifact) for artifact in event.get("artifacts", []))
                ],
            )
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "index_kind": "sqlite_fts5_bm25_relation_index",
            "semantic_backend": "unavailable_no_local_sentence_transformers_or_faiss",
            "source_count": str(len(sources)),
            "event_count": str(len(canonical_events)),
            "reference_count": str(len(reference_corpus.events)),
            "record_count": str(len(events)),
            "reference_source_bundle_sha256": reference_corpus.fingerprint,
            "reference_extracted_count": str(reference_corpus.extracted_count),
            "reference_reused_count": str(reference_corpus.reused_count),
            "reference_manifest_count": str(reference_corpus.manifest_count),
            "reference_auxiliary_count": str(reference_corpus.auxiliary_count),
            "reference_pdf_count": str(reference_corpus.pdf_count),
            "reference_pdf_page_count": str(reference_corpus.pdf_page_count),
            "reference_sparse_pdf_page_count": str(reference_corpus.sparse_pdf_page_count),
            "reference_ocr_pdf_page_count": str(reference_corpus.ocr_pdf_page_count),
            "reference_extraction_error_count": str(reference_corpus.extraction_error_count),
            "source_range_correction_count": str(len(source_corrections)),
        }
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        connection.commit()
    connection.close()
    return {"output": str(output), **{key: value for key, value in metadata.items()}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.events_root.resolve(), args.output.resolve()), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
