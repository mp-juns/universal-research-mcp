#!/usr/bin/env python3
"""Build a derived SQLite FTS5 index from the append-only research-event JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universal_research_mcp.tools.research_reference_corpus import build_reference_corpus
from universal_research_mcp.tools.research_event_corrections import apply_source_range_corrections
from universal_research_mcp.core.ledger import validate_records
from universal_research_mcp.core.amendments import resolve_core_amendments
from universal_research_mcp.core.indexing import index_document, index_document_id, is_core_record


SCHEMA_VERSION = "1.0"


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
        CREATE VIRTUAL TABLE source_passage_fts USING fts5(
            event_id UNINDEXED,
            source_path UNINDEXED,
            source_sha256 UNINDEXED,
            line_start UNINDEXED,
            line_end UNINDEXED,
            content,
            tokenize = 'unicode61'
        );
        CREATE VIRTUAL TABLE event_fts USING fts5(
            event_id UNINDEXED,
            summary,
            source_heading,
            source_path,
            tokenize = 'unicode61'
        );
        """
    )


def _infer_project_root(events_root: Path) -> Path:
    """Keep legacy layouts while recognizing the packaged data/events layout."""

    resolved = events_root.resolve()
    if resolved.name == "events" and resolved.parent.name == "data":
        return resolved.parent.parent
    return resolved.parent


def _source_passage(
    project_root: Path,
    source: dict,
    registered_hashes: dict[str, set[str]],
) -> tuple[str, str, int, int, str] | None:
    """Return one registered, line-addressable passage for an indexed event.

    The derived index never crawls arbitrary project files.  It reads only a
    source path that both the event and canonical source registry bind to the
    same SHA-256, then keeps the event's already-authorized line range.
    """
    path_text = source.get("source_path")
    source_hash = source.get("source_sha256")
    start = source.get("line_start")
    end = source.get("line_end")
    if not isinstance(path_text, str) or not isinstance(source_hash, str):
        return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", source_hash) is None:
        # Compatibility fixtures may carry legacy placeholder hashes. They are
        # retained in the event projection but cannot become source passages.
        return None
    if source_hash.lower() not in registered_hashes.get(path_text, set()):
        return None
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return None
    candidate = (project_root / path_text).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"indexed source path escapes project root: {path_text}") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"indexed source is missing: {path_text}")
    snapshot = candidate.read_bytes()
    if hashlib.sha256(snapshot).hexdigest().lower() != source_hash.lower():
        raise ValueError(f"indexed source hash does not match current file: {path_text}")
    lines = snapshot.decode("utf-8", errors="replace").splitlines()
    if start > len(lines):
        raise ValueError(f"indexed source range begins after EOF: {path_text}:{start}")
    if end > len(lines):
        raise ValueError(f"indexed source range ends after EOF: {path_text}:{end}")
    content = "\n".join(lines[start - 1:end])
    if not content.strip():
        return None
    return path_text, source_hash, start, end, content


def build(
    events_root: Path,
    output: Path,
    project_root: Path | None = None,
) -> dict:
    events_root = events_root.resolve()
    project_root = (
        project_root.resolve()
        if project_root is not None
        else _infer_project_root(events_root)
    )
    sources_path = events_root / "sources.jsonl"
    if not sources_path.exists():
        raise FileNotFoundError(f"Missing source manifest: {sources_path}")
    event_paths = sorted((events_root / "daily").glob("*/events.jsonl"))
    if not event_paths:
        raise FileNotFoundError(f"No daily event JSONL under: {events_root / 'daily'}")
    sources = read_jsonl(sources_path)
    registered_hashes: dict[str, set[str]] = {}
    for source in sources:
        path_text = source.get("source_path")
        source_hash = source.get("source_sha256")
        if isinstance(path_text, str) and isinstance(source_hash, str):
            registered_hashes.setdefault(path_text, set()).add(source_hash.lower())
    canonical_events = [event for path in event_paths for event in read_jsonl(path)]
    validation_issues = validate_records(canonical_events)
    if validation_issues:
        rendered = "; ".join(
            f"{issue.record_id}{issue.path}: {issue.message}"
            for issue in validation_issues
        )
        raise ValueError(f"Canonical ledger validation failed: {rendered}")
    legacy_events = [event for event in canonical_events if not is_core_record(event)]
    core_events = [event for event in canonical_events if is_core_record(event)]
    resolved_legacy_events, source_corrections = apply_source_range_corrections(
        legacy_events
    )
    resolved_core_events, core_corrections = resolve_core_amendments(core_events)
    resolved_by_id = {
        index_document_id(event): event
        for event in [*resolved_legacy_events, *resolved_core_events]
    }
    reference_corpus = build_reference_corpus(project_root)
    events = [*canonical_events, *reference_corpus.events]
    event_ids = [index_document_id(event) for event in events]
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
            identifier = index_document_id(event)
            effective_event = index_document(resolved_by_id.get(identifier, event))
            source = effective_event.get("source", {})
            connection.execute(
                """
                INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effective_event["event_id"],
                    effective_event["date"],
                    effective_event["event_type"],
                    effective_event["status"],
                    effective_event["project"],
                    effective_event.get("workstream"),
                    effective_event["summary"],
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
            passage = _source_passage(project_root, source, registered_hashes)
            if passage is not None:
                connection.execute(
                    "INSERT INTO source_passage_fts VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        effective_event["event_id"],
                        passage[0],
                        passage[1],
                        passage[2],
                        passage[3],
                        passage[4],
                    ),
                )
            connection.execute(
                "INSERT INTO event_fts VALUES (?, ?, ?, ?)",
                (
                    effective_event["event_id"],
                    effective_event["summary"],
                    source.get("heading", ""),
                    source.get("source_path", ""),
                ),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO relations VALUES (?, ?, ?)",
                [
                    (
                        effective_event["event_id"],
                        normalized.get("type", "unknown"),
                        normalized.get("target", "unknown"),
                    )
                    for normalized in (
                        normalize_relation(
                            relation, event_id=effective_event["event_id"]
                        )
                        for relation in effective_event.get("relations", [])
                    )
                ],
            )
            connection.executemany(
                "INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?)",
                [
                    (
                        effective_event["event_id"],
                        normalized["path"],
                        normalized.get("sha256"),
                        normalized.get("role"),
                    )
                    for normalized in (
                        normalize_artifact(artifact)
                        for artifact in effective_event.get("artifacts", [])
                    )
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
            "reference_sparse_pdf_page_count": str(
                reference_corpus.sparse_pdf_page_count
            ),
            "reference_ocr_pdf_page_count": str(reference_corpus.ocr_pdf_page_count),
            "reference_extraction_error_count": str(
                reference_corpus.extraction_error_count
            ),
            "source_range_correction_count": str(len(source_corrections)),
            "core_amendment_correction_count": str(len(core_corrections)),
        }
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        connection.commit()
    connection.close()
    return {"output": str(output), **{key: value for key, value in metadata.items()}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.events_root.resolve(),
                args.output.resolve(),
                project_root=(
                    args.project_root.resolve()
                    if args.project_root is not None
                    else None
                ),
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
