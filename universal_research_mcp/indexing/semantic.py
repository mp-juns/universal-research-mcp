"""Provider-neutral, provenance-bounded semantic index lifecycle.

The lexical SQLite database is the only row source.  Embedding inputs contain
only concise indexed event fields and exact, hash-verified source line slices;
``events.raw_json`` and unbounded files are never sent to an embedder.  The
semantic database is derived state and is promoted only after full validation.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import tempfile
from typing import Any, Mapping, Protocol, Sequence

from core.index_refresh import validate_index_health_record
from universal_research_mcp.indexing.lexical import FINGERPRINT_KEY, index_status
from universal_research_mcp.providers.redaction import REDACTED, redact_text
from universal_research_mcp.runtime import ProjectPaths


SCHEMA_VERSION = "semantic-index/1.0"
SEMANTIC_DB_NAME = "semantic.sqlite"
INPUT_FINGERPRINT_VERSION = "semantic-input-sha256-v1"
MAX_FIELD_CHARS = 3_000
MAX_SOURCE_LINES = 200
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_LINE_NUMBER = 1_000_000
MAX_PASSAGE_CHARS = 3_500
MAX_PASSAGES_PER_EVENT = 8
DEFAULT_BATCH_SIZE = 32
MAX_BATCH_SIZE = 256
REQUIRED_TABLES = frozenset({"metadata", "embeddings", "passage_embeddings"})
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_LIKELY_BARE_CREDENTIAL = re.compile(
    r"\b(?:sk-(?:ant-(?:api\d{2}-)?)?[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,})\b"
)


class SemanticEmbedder(Protocol):
    """Injected execution boundary; policy approval happens before this call."""

    def embed(
        self,
        texts: tuple[str, ...],
        *,
        model: str,
        dimensions: int | None,
    ) -> Sequence[Sequence[float]]: ...


@dataclass(frozen=True)
class _EventDocument:
    event_id: str
    text: str
    text_sha256: str


@dataclass(frozen=True)
class _PassageDocument:
    passage_id: str
    event_id: str
    source_path: str
    source_heading: str
    line_start: int
    line_end: int
    text: str
    text_sha256: str


@dataclass(frozen=True)
class _SemanticInput:
    lexical_fingerprint: str
    input_fingerprint: str
    events: tuple[_EventDocument, ...]
    passages: tuple[_PassageDocument, ...]


def _semantic_path(paths: ProjectPaths) -> Path:
    return paths.semantic_db


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bounded_text(value: Any, maximum: int = MAX_FIELD_CHARS) -> str:
    if value is None:
        return ""
    rendered = str(value).replace("\x00", "").strip()
    return rendered[:maximum]


def _event_text(row: Mapping[str, Any]) -> str:
    """Render only allowlisted lexical columns, never ``raw_json``."""

    heading = _bounded_text(row.get("source_heading"), 500)
    event_id = _bounded_text(row.get("event_id"), 500)
    fields = [
        ("Title", heading or event_id),
        ("Event type", _bounded_text(row.get("event_type"), 200)),
        ("Status", _bounded_text(row.get("status"), 200)),
        ("Project", _bounded_text(row.get("project"), 500)),
        ("Workstream", _bounded_text(row.get("workstream"), 500)),
        ("Summary", _bounded_text(row.get("summary"))),
    ]
    return "\n".join(f"{label}: {value}" for label, value in fields if value)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_source_path(
    paths: ProjectPaths,
    source_path: Any,
    source_sha256: Any,
) -> Path | None:
    path_text = _bounded_text(source_path, 2_000)
    expected_hash = _bounded_text(source_sha256, 128)
    if not path_text or not _SHA256.fullmatch(expected_hash):
        return None
    try:
        candidate = paths.resolve_relative(path_text)
    except (OSError, ValueError):
        return None
    try:
        candidate.relative_to(paths.events_root)
    except ValueError:
        pass
    else:
        # Canonical JSONL can contain prompts, chats, and internal records.
        # Its approved summary columns are already represented by _event_text.
        return None
    if not candidate.is_file():
        return None
    try:
        if candidate.stat().st_size > MAX_SOURCE_BYTES:
            return None
        if _hash_file(candidate).lower() != expected_hash.lower():
            return None
    except OSError:
        return None
    return candidate


def _line_range(row: Mapping[str, Any]) -> tuple[int, int] | None:
    start = row.get("line_start")
    end = row.get("line_end")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 1
        or end < start
        or start > MAX_LINE_NUMBER
    ):
        return None
    return start, min(end, start + MAX_SOURCE_LINES - 1)


def _read_numbered_lines(path: Path, start: int, end: int) -> list[tuple[int, str]]:
    selected: list[tuple[int, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for number, raw in enumerate(handle, start=1):
            if number < start:
                continue
            if number > end:
                break
            selected.append((number, raw.rstrip("\r\n").replace("\x00", "")))
    return selected


def _source_passages(
    row: Mapping[str, Any],
    paths: ProjectPaths,
) -> tuple[_PassageDocument, ...]:
    if not bool(row.get("source_registered")):
        return ()
    bounded_range = _line_range(row)
    source = _safe_source_path(paths, row.get("source_path"), row.get("source_sha256"))
    if bounded_range is None or source is None:
        return ()
    start, end = bounded_range
    try:
        numbered = _read_numbered_lines(source, start, end)
    except OSError:
        return ()
    if not numbered:
        return ()

    chunks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_chars = 0
    for number, raw in numbered:
        # A single pathological line remains line-addressable but cannot create
        # an unbounded provider payload.
        line = raw[:MAX_PASSAGE_CHARS]
        added = len(line) + (1 if current else 0)
        if current and current_chars + added > MAX_PASSAGE_CHARS:
            chunks.append(current)
            current = []
            current_chars = 0
            if len(chunks) >= MAX_PASSAGES_PER_EVENT:
                break
        current.append((number, line))
        current_chars += len(line) + (1 if len(current) > 1 else 0)
    if current and len(chunks) < MAX_PASSAGES_PER_EVENT:
        chunks.append(current)

    path_text = _bounded_text(row.get("source_path"), 2_000)
    heading = _bounded_text(row.get("source_heading"), 500) or _bounded_text(
        row.get("event_id"), 500
    )
    event_id = str(row["event_id"])
    passages: list[_PassageDocument] = []
    for chunk in chunks:
        line_start = chunk[0][0]
        line_end = chunk[-1][0]
        evidence = "\n".join(text for _, text in chunk).strip()
        if not evidence:
            continue
        text = "\n".join(
            (
                f"Section heading: {heading}",
                f"Source path: {path_text}",
                f"Evidence lines {line_start}-{line_end}: {evidence}",
            )
        )
        identity = (
            f"{event_id}\0{path_text}\0{line_start}\0{line_end}\0{text}"
        ).encode("utf-8")
        passages.append(
            _PassageDocument(
                passage_id=f"psg_{_sha256(identity)[:20]}",
                event_id=event_id,
                source_path=path_text,
                source_heading=heading,
                line_start=line_start,
                line_end=line_end,
                text=text,
                text_sha256=_sha256(text.encode("utf-8")),
            )
        )
    return tuple(passages)


def _load_semantic_input(root: str | Path) -> _SemanticInput:
    paths = ProjectPaths.from_root(root)
    lexical = index_status(paths.root)
    if lexical.get("status") != "current":
        raise RuntimeError("lexical index must be current before semantic indexing")
    if not paths.lexical_db.is_file():
        raise RuntimeError("lexical index is missing")

    with closing(
        sqlite3.connect(f"file:{paths.lexical_db.as_posix()}?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        lexical_fingerprint = metadata.get(FINGERPRINT_KEY)
        if lexical_fingerprint != lexical.get("current_fingerprint"):
            raise RuntimeError("lexical index fingerprint changed during semantic planning")
        rows = connection.execute(
            """
            SELECT e.event_id, e.event_type, e.status, e.project, e.workstream,
                   e.summary, e.source_path, e.source_heading, e.source_sha256,
                   e.line_start, e.line_end,
                   EXISTS(
                       SELECT 1 FROM sources AS s
                       WHERE s.source_path = e.source_path
                         AND lower(s.source_sha256) = lower(e.source_sha256)
                   ) AS source_registered
            FROM events AS e
            ORDER BY event_id
            """
        ).fetchall()

    events: list[_EventDocument] = []
    passages: list[_PassageDocument] = []
    for sqlite_row in rows:
        row = dict(sqlite_row)
        text = _event_text(row)
        events.append(
            _EventDocument(
                event_id=str(row["event_id"]),
                text=text,
                text_sha256=_sha256(text.encode("utf-8")),
            )
        )
        passages.extend(_source_passages(row, paths))

    digest = hashlib.sha256()
    digest.update(INPUT_FINGERPRINT_VERSION.encode("ascii") + b"\0")
    digest.update(str(lexical_fingerprint).encode("ascii") + b"\0")
    for document in events:
        digest.update(
            f"event\0{document.event_id}\0{document.text_sha256}\n".encode("utf-8")
        )
    for passage in sorted(passages, key=lambda item: item.passage_id):
        digest.update(
            (
                f"passage\0{passage.passage_id}\0{passage.event_id}\0"
                f"{passage.source_path}\0{passage.line_start}\0{passage.line_end}\0"
                f"{passage.text_sha256}\n"
            ).encode("utf-8")
        )
    return _SemanticInput(
        lexical_fingerprint=str(lexical_fingerprint),
        input_fingerprint=digest.hexdigest(),
        events=tuple(events),
        passages=tuple(sorted(passages, key=lambda item: item.passage_id)),
    )


def normalize_vector(
    raw: Sequence[float],
    *,
    dimensions: int | None = None,
) -> tuple[float, ...]:
    """Return a finite unit vector without requiring NumPy."""

    if isinstance(raw, (str, bytes, bytearray)):
        raise ValueError("embedding vector must be a numeric sequence")
    try:
        vector = tuple(float(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("embedding vector contains a non-numeric value") from exc
    if not vector:
        raise ValueError("embedding vector must not be empty")
    if dimensions is not None and len(vector) != dimensions:
        raise ValueError(
            f"embedding dimension mismatch: expected {dimensions}, received {len(vector)}"
        )
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("embedding vector contains a non-finite value")
    scale = max(abs(value) for value in vector)
    if scale == 0:
        raise ValueError("embedding vector has zero norm")
    norm = scale * math.sqrt(math.fsum((value / scale) ** 2 for value in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("embedding vector has an invalid norm")
    normalized = tuple(value / norm for value in vector)
    if not all(math.isfinite(value) for value in normalized):
        raise ValueError("normalized embedding vector is non-finite")
    return normalized


def _invoke_embedder(
    embedder: SemanticEmbedder,
    texts: tuple[str, ...],
    *,
    provider_id: str,
    model: str,
    dimensions: int | None,
) -> Sequence[Sequence[float]]:
    method = getattr(embedder, "embed", None)
    if not callable(method):
        raise TypeError("embedder must provide an embed(texts, model=, dimensions=) method")
    result = method(texts, model=model, dimensions=dimensions)
    resolved_provider = getattr(result, "provider_id", provider_id)
    resolved_model = getattr(result, "model", model)
    if resolved_provider != provider_id:
        raise ValueError("embedder result provider does not match the approved provider")
    if resolved_model != model:
        raise ValueError("embedder result model does not match the approved model")
    return getattr(result, "vectors", result)


def _embed_documents(
    embedder: SemanticEmbedder,
    texts: tuple[str, ...],
    *,
    provider_id: str,
    model: str,
    dimensions: int | None,
    batch_size: int,
) -> tuple[tuple[tuple[float, ...], ...], int]:
    vectors: list[tuple[float, ...]] = []
    effective_dimensions = dimensions
    for offset in range(0, len(texts), batch_size):
        batch = texts[offset : offset + batch_size]
        raw_vectors = _invoke_embedder(
            embedder,
            batch,
            provider_id=provider_id,
            model=model,
            dimensions=dimensions,
        )
        try:
            materialized = tuple(raw_vectors)
        except TypeError as exc:
            raise ValueError("embedder result is not a vector sequence") from exc
        if len(materialized) != len(batch):
            raise ValueError(
                "embedding count mismatch: "
                f"expected {len(batch)}, received {len(materialized)}"
            )
        for raw in materialized:
            normalized = normalize_vector(raw, dimensions=effective_dimensions)
            if effective_dimensions is None:
                effective_dimensions = len(normalized)
            vectors.append(normalized)
    if effective_dimensions is None:
        effective_dimensions = 0
    return tuple(vectors), effective_dimensions


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = DELETE;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE embeddings (
            event_id TEXT PRIMARY KEY,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            retrieval_text_sha256 TEXT NOT NULL
        );
        CREATE TABLE passage_embeddings (
            passage_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_heading TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            retrieval_text_sha256 TEXT NOT NULL
        );
        CREATE INDEX passage_event_idx ON passage_embeddings(event_id);
        """
    )


def _vector_blob(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _write_staging(
    output: Path,
    semantic_input: _SemanticInput,
    vectors: tuple[tuple[float, ...], ...],
    *,
    provider_id: str,
    model: str,
    dimensions: int,
) -> None:
    event_count = len(semantic_input.events)
    event_vectors = vectors[:event_count]
    passage_vectors = vectors[event_count:]
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "index_kind": "provider_neutral_dense_cosine_research_memory",
        "canonical_bundle_sha256": semantic_input.lexical_fingerprint,
        "lexical_fingerprint": semantic_input.lexical_fingerprint,
        "semantic_input_sha256": semantic_input.input_fingerprint,
        "semantic_input_fingerprint_algorithm": INPUT_FINGERPRINT_VERSION,
        "provider_id": provider_id,
        "model": model,
        "dimensions": str(dimensions),
        "event_count": str(event_count),
        "embedding_count": str(event_count),
        "passage_count": str(len(semantic_input.passages)),
        "index_built_at": datetime.now(timezone.utc).isoformat(),
    }
    with closing(sqlite3.connect(output)) as connection:
        with connection:
            _initialize_schema(connection)
            connection.executemany(
                "INSERT INTO embeddings VALUES (?, ?, ?, ?)",
                (
                    (
                        document.event_id,
                        dimensions,
                        _vector_blob(vector),
                        document.text_sha256,
                    )
                    for document, vector in zip(
                        semantic_input.events, event_vectors, strict=True
                    )
                ),
            )
            connection.executemany(
                "INSERT INTO passage_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        passage.passage_id,
                        passage.event_id,
                        passage.source_path,
                        passage.source_heading,
                        passage.line_start,
                        passage.line_end,
                        dimensions,
                        _vector_blob(vector),
                        passage.text_sha256,
                    )
                    for passage, vector in zip(
                        semantic_input.passages, passage_vectors, strict=True
                    )
                ),
            )
            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)", metadata.items()
            )


def _verify_vectors(
    rows: Sequence[tuple[int, bytes]],
    *,
    dimensions: int,
) -> None:
    for row_dimensions, blob in rows:
        if row_dimensions != dimensions or len(blob) != dimensions * 4:
            raise RuntimeError("semantic vector dimension/blob mismatch")
        values = struct.unpack(f"<{dimensions}f", blob)
        if not all(math.isfinite(value) for value in values):
            raise RuntimeError("semantic database contains a non-finite vector")
        norm = math.sqrt(math.fsum(value * value for value in values))
        if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise RuntimeError("semantic database contains a non-normalized vector")


def verify_semantic_index(
    database: Path,
    semantic_input: _SemanticInput,
    *,
    provider_id: str,
    model: str,
    dimensions: int,
) -> dict[str, Any]:
    if not database.is_file():
        raise RuntimeError(f"staged semantic index was not created: {database}")
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise RuntimeError(f"semantic index integrity check failed: {integrity}")
        tables = {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise RuntimeError(f"semantic index is missing required tables: {missing}")
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
        expected_metadata = {
            "schema_version": SCHEMA_VERSION,
            "lexical_fingerprint": semantic_input.lexical_fingerprint,
            "semantic_input_sha256": semantic_input.input_fingerprint,
            "provider_id": provider_id,
            "model": model,
            "dimensions": str(dimensions),
            "embedding_count": str(len(semantic_input.events)),
            "passage_count": str(len(semantic_input.passages)),
        }
        mismatched = sorted(
            key for key, value in expected_metadata.items() if metadata.get(key) != value
        )
        if mismatched:
            raise RuntimeError(f"semantic index metadata mismatch: {mismatched}")

        event_rows = db.execute(
            "SELECT event_id, dimensions, vector, retrieval_text_sha256 "
            "FROM embeddings ORDER BY event_id"
        ).fetchall()
        expected_events = [
            (document.event_id, document.text_sha256)
            for document in semantic_input.events
        ]
        if [(row[0], row[3]) for row in event_rows] != expected_events:
            raise RuntimeError("semantic event identifiers or text hashes mismatch")

        passage_rows = db.execute(
            """
            SELECT passage_id, event_id, source_path, source_heading,
                   line_start, line_end, dimensions, vector,
                   retrieval_text_sha256
            FROM passage_embeddings ORDER BY passage_id
            """
        ).fetchall()
        expected_passages = [
            (
                passage.passage_id,
                passage.event_id,
                passage.source_path,
                passage.source_heading,
                passage.line_start,
                passage.line_end,
                passage.text_sha256,
            )
            for passage in semantic_input.passages
        ]
        if [
            (row[0], row[1], row[2], row[3], row[4], row[5], row[8])
            for row in passage_rows
        ] != expected_passages:
            raise RuntimeError("semantic passage provenance or text hashes mismatch")
        if dimensions == 0:
            if event_rows or passage_rows:
                raise RuntimeError("zero-dimensional semantic index contains vectors")
        else:
            _verify_vectors(
                [(int(row[1]), bytes(row[2])) for row in event_rows],
                dimensions=dimensions,
            )
            _verify_vectors(
                [(int(row[6]), bytes(row[7])) for row in passage_rows],
                dimensions=dimensions,
            )
    return {
        "integrity": integrity,
        "embedding_count": len(event_rows),
        "passage_count": len(passage_rows),
        "dimensions": dimensions,
        "input_fingerprint": semantic_input.input_fingerprint,
    }


def _database_snapshot(paths: ProjectPaths) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "usable": False,
        "integrity": None,
        "event_count": 0,
        "passage_count": 0,
        "dimensions": 0,
        "provider_id": None,
        "model": None,
        "source_event_ids": [],
        "artifact_hashes": [],
    }
    database = paths.semantic_db
    if not database.is_file():
        return snapshot
    try:
        snapshot["artifact_hashes"] = [
            {
                "path": database.relative_to(paths.root).as_posix(),
                "sha256": _hash_file(database),
            }
        ]
        with closing(
            sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        ) as db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
            if integrity != "ok" or not REQUIRED_TABLES.issubset(tables):
                snapshot["integrity"] = integrity
                return snapshot
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
            dimensions = int(metadata.get("dimensions", "0"))
            event_rows = db.execute(
                "SELECT event_id FROM embeddings ORDER BY event_id"
            ).fetchall()
            passage_count = int(
                db.execute("SELECT COUNT(*) FROM passage_embeddings").fetchone()[0]
            )
        snapshot.update(
            {
                "usable": dimensions >= 0,
                "integrity": integrity,
                "event_count": len(event_rows),
                "passage_count": passage_count,
                "dimensions": max(0, dimensions),
                "provider_id": metadata.get("provider_id"),
                "model": metadata.get("model"),
                "source_event_ids": [str(row[0]) for row in event_rows],
            }
        )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        snapshot["usable"] = False
    return snapshot


def _decode_vector(blob: bytes, dimensions: int) -> tuple[float, ...]:
    if dimensions < 1 or len(blob) != dimensions * 4:
        raise RuntimeError("retrieval verification vector shape mismatch")
    vector = struct.unpack(f"<{dimensions}f", blob)
    if not all(math.isfinite(value) for value in vector):
        raise RuntimeError("retrieval verification found a non-finite vector")
    return vector


def _retrieval_verification(
    paths: ProjectPaths,
    database: Path,
    semantic_input: _SemanticInput,
    *,
    dimensions: int,
) -> dict[str, Any]:
    """Self-query one latest event and refetch its canonical lexical record."""

    if not semantic_input.events:
        return {
            "status": "passed",
            "mode": "empty_index",
            "candidate_found": True,
            "canonical_event_fetched": True,
            "canonical_source_fetched": False,
            "source_slice_fetched": False,
        }
    if dimensions < 1:
        raise RuntimeError("non-empty semantic index has no embedding dimensions")

    with closing(
        sqlite3.connect(f"file:{paths.lexical_db.as_posix()}?mode=ro", uri=True)
    ) as lexical:
        lexical.row_factory = sqlite3.Row
        latest = lexical.execute(
            """
            SELECT e.event_id
            FROM events AS e
            JOIN sources AS s
              ON s.source_path = e.source_path
             AND lower(s.source_sha256) = lower(e.source_sha256)
            ORDER BY e.date DESC, e.rowid DESC
            LIMIT 1
            """
        ).fetchone()
        if latest is None:
            raise RuntimeError("retrieval verification could not select a canonical event")
        target_id = str(latest["event_id"])
        canonical = lexical.execute(
            """
            SELECT event_id, event_type, status, project, workstream, summary,
                   source_path, source_heading, source_sha256, line_start, line_end
            FROM events WHERE event_id = ?
            """,
            (target_id,),
        ).fetchone()
    expected = next(
        (document for document in semantic_input.events if document.event_id == target_id),
        None,
    )
    if canonical is None or expected is None:
        raise RuntimeError("retrieval verification could not refetch the canonical event")
    canonical_hash = _sha256(_event_text(dict(canonical)).encode("utf-8"))
    if canonical_hash != expected.text_sha256:
        raise RuntimeError("retrieval verification canonical event hash mismatch")
    canonical_source = _safe_source_path(
        paths,
        canonical["source_path"],
        canonical["source_sha256"],
    )
    if canonical_source is None:
        raise RuntimeError("retrieval verification could not refetch canonical source")

    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT event_id, dimensions, vector FROM embeddings ORDER BY event_id"
        ).fetchall()
        by_id = {
            str(event_id): _decode_vector(bytes(blob), int(row_dimensions))
            for event_id, row_dimensions, blob in rows
        }
        query = by_id.get(target_id)
        if query is None:
            raise RuntimeError("retrieval verification target is absent from embeddings")
        scores = {
            event_id: math.fsum(left * right for left, right in zip(query, vector, strict=True))
            for event_id, vector in by_id.items()
        }
        best = max(scores.values())
        candidates = sorted(
            event_id
            for event_id, score in scores.items()
            if math.isclose(score, best, rel_tol=1e-6, abs_tol=1e-6)
        )
        if target_id not in candidates:
            raise RuntimeError("retrieval verification did not recover its query event")

        target_passages = tuple(
            passage
            for passage in semantic_input.passages
            if passage.event_id == target_id
        )
        source_slice_fetched = False
        if target_passages:
            first = target_passages[0]
            stored = db.execute(
                """
                SELECT source_path, line_start, line_end, retrieval_text_sha256
                FROM passage_embeddings WHERE passage_id = ?
                """,
                (first.passage_id,),
            ).fetchone()
            source_slice_fetched = stored == (
                first.source_path,
                first.line_start,
                first.line_end,
                first.text_sha256,
            )
            if not source_slice_fetched:
                raise RuntimeError("retrieval verification source-slice fetch mismatch")
    return {
        "status": "passed",
        "mode": "self_similarity_plus_canonical_refetch",
        "query_event_id": target_id,
        "retrieved_event_id": target_id,
        "top_score": best,
        "candidate_found": True,
        "canonical_event_fetched": True,
        "canonical_source_fetched": True,
        "source_slice_fetched": source_slice_fetched,
    }


def _health_model(provider_id: Any, model: Any) -> str:
    provider = _LIKELY_BARE_CREDENTIAL.sub(
        REDACTED, redact_text(provider_id or "unavailable")
    )[:200]
    model_name = _LIKELY_BARE_CREDENTIAL.sub(
        REDACTED, redact_text(model or "unavailable")
    )[:500]
    rendered = f"{provider}:{model_name}".strip(":")
    return rendered or "unavailable"


def _health_record(
    paths: ProjectPaths,
    semantic_input: _SemanticInput,
    *,
    provider_id: str,
    model: str,
    dimensions: int,
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    snapshot = _database_snapshot(paths)
    if not snapshot["usable"]:
        raise RuntimeError("promoted semantic database is not health-record eligible")
    return {
        "schema_version": "research-index-health-v1",
        "status": "succeeded",
        "index_revision": semantic_input.input_fingerprint,
        "event_count": snapshot["event_count"],
        "passage_count": snapshot["passage_count"],
        "embedding_model": _health_model(provider_id, model),
        "embedding_dimension": dimensions,
        "artifact_hashes": snapshot["artifact_hashes"],
        "source_event_ids": snapshot["source_event_ids"],
        "retrieval_verification": retrieval,
        "failures": [],
    }


def _sanitized_failure(error: Exception) -> dict[str, str]:
    message = redact_text(error)
    message = _LIKELY_BARE_CREDENTIAL.sub(REDACTED, message)[:1_000]
    return {"failure_type": type(error).__name__, "message": message}


def _failure_health_record(
    paths: ProjectPaths,
    semantic_input: _SemanticInput | None,
    before_snapshot: dict[str, Any],
    *,
    provider_id: Any,
    model: Any,
    dimensions: int | None,
    error: Exception,
) -> dict[str, Any]:
    after = _database_snapshot(paths)
    before_hashes = before_snapshot.get("artifact_hashes") or []
    after_hashes = after.get("artifact_hashes") or []
    previous_preserved = bool(
        before_snapshot.get("usable")
        and before_hashes
        and before_hashes == after_hashes
    )
    revision = (
        semantic_input.input_fingerprint
        if semantic_input is not None
        else str(index_status(paths.root).get("current_fingerprint") or "unavailable")
    )
    if after.get("usable"):
        effective_dimensions = after.get("dimensions", 0)
    else:
        effective_dimensions = (
            dimensions if isinstance(dimensions, int) and dimensions > 0 else 0
        )
    if not isinstance(effective_dimensions, int) or effective_dimensions < 0:
        effective_dimensions = 0
    return {
        "schema_version": "research-index-health-v1",
        "status": "stale" if after.get("usable") else "failed",
        "index_revision": revision,
        "event_count": int(after.get("event_count", 0)),
        "passage_count": int(after.get("passage_count", 0)),
        "embedding_model": _health_model(provider_id, model),
        "embedding_dimension": effective_dimensions,
        "artifact_hashes": after_hashes,
        "source_event_ids": list(after.get("source_event_ids", [])),
        "retrieval_verification": {
            "status": "failed",
            "previous_good_database_preserved": previous_preserved,
        },
        "failures": [_sanitized_failure(error)],
    }


def _atomic_write_health(path: Path, record: dict[str, Any]) -> None:
    issues = validate_index_health_record(record)
    if issues:
        raise RuntimeError("semantic health record validation failed: " + "; ".join(issues))
    payload = (
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
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
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_health(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "missing"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "invalid"
    if not isinstance(loaded, dict) or validate_index_health_record(loaded):
        return None, "invalid"
    return loaded, None


def _validate_identity(
    provider_id: str | None,
    model: str | None,
    dimensions: int | None,
) -> None:
    if provider_id is not None and (
        not isinstance(provider_id, str) or not provider_id.strip()
    ):
        raise ValueError("provider_id must not be empty")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("model must not be empty")
    if dimensions is not None and (
        isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1
    ):
        raise ValueError("dimensions must be a positive integer when supplied")


def semantic_status(
    root: str | Path,
    *,
    provider_id: str | None = None,
    model: str | None = None,
    dimensions: int | None = None,
) -> dict[str, Any]:
    """Report ``missing``, ``current``, or ``stale`` without running a model."""

    _validate_identity(provider_id, model, dimensions)
    paths = ProjectPaths.from_root(root)
    database = _semantic_path(paths)
    health, health_error = _read_health(paths.semantic_health)
    base: dict[str, Any] = {
        "semantic_db": str(database),
        "semantic_health": str(paths.semantic_health),
        "health_status": health.get("status") if health is not None else health_error,
    }
    try:
        semantic_input = _load_semantic_input(paths.root)
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        status = "missing" if not database.is_file() else "stale"
        return {
            **base,
            "status": status,
            "stale": True,
            "reason": str(exc),
            "current_fingerprint": None,
            "indexed_fingerprint": None,
        }
    base.update(
        {
            "current_fingerprint": semantic_input.input_fingerprint,
            "lexical_fingerprint": semantic_input.lexical_fingerprint,
            "event_count": len(semantic_input.events),
            "passage_count": len(semantic_input.passages),
        }
    )
    if not database.is_file():
        return {
            **base,
            "status": "missing",
            "stale": True,
            "indexed_fingerprint": None,
        }
    try:
        with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
            embedding_count = int(db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
            passage_count = int(
                db.execute("SELECT COUNT(*) FROM passage_embeddings").fetchone()[0]
            )
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return {
            **base,
            "status": "stale",
            "stale": True,
            "reason": f"invalid semantic index: {type(exc).__name__}",
            "indexed_fingerprint": None,
        }
    try:
        stored_dimensions = int(metadata.get("dimensions", ""))
    except (TypeError, ValueError):
        stored_dimensions = -1
    checks = {
        "integrity": integrity == "ok",
        "schema": metadata.get("schema_version") == SCHEMA_VERSION,
        "lexical_fingerprint": metadata.get("lexical_fingerprint")
        == semantic_input.lexical_fingerprint,
        "input_fingerprint": metadata.get("semantic_input_sha256")
        == semantic_input.input_fingerprint,
        "event_count": embedding_count == len(semantic_input.events),
        "passage_count": passage_count == len(semantic_input.passages),
        "dimensions_metadata": stored_dimensions >= 0,
    }
    if stored_dimensions >= 0:
        try:
            verify_semantic_index(
                database,
                semantic_input,
                provider_id=str(metadata.get("provider_id", "")),
                model=str(metadata.get("model", "")),
                dimensions=stored_dimensions,
            )
        except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError):
            checks["verification"] = False
    if provider_id is not None:
        checks["provider_id"] = metadata.get("provider_id") == provider_id
    if model is not None:
        checks["model"] = metadata.get("model") == model
    if dimensions is not None:
        checks["dimensions"] = metadata.get("dimensions") == str(dimensions)
    structural_current = all(checks.values())
    snapshot = _database_snapshot(paths)
    health_current = bool(
        health
        and health.get("status") == "succeeded"
        and health.get("index_revision") == semantic_input.input_fingerprint
        and health.get("embedding_model")
        == _health_model(metadata.get("provider_id"), metadata.get("model"))
        and health.get("embedding_dimension") == stored_dimensions
        and health.get("event_count") == len(semantic_input.events)
        and health.get("passage_count") == len(semantic_input.passages)
        and health.get("artifact_hashes") == snapshot.get("artifact_hashes")
        and health.get("source_event_ids") == snapshot.get("source_event_ids")
        and isinstance(health.get("retrieval_verification"), dict)
        and health["retrieval_verification"].get("status") == "passed"
    )
    checks["health_record"] = health_current
    current = structural_current and health_current
    return {
        **base,
        "status": "current" if current else "stale",
        "stale": not current,
        "indexed_fingerprint": metadata.get("semantic_input_sha256"),
        "provider_id": metadata.get("provider_id"),
        "model": metadata.get("model"),
        "dimensions": stored_dimensions if stored_dimensions >= 0 else None,
        "integrity": integrity,
        "structural_current": structural_current,
        "failed_checks": sorted(key for key, passed in checks.items() if not passed),
    }


def _sync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        os.close(descriptor)


def ensure_semantic_index(
    root: str | Path,
    embedder: SemanticEmbedder,
    *,
    provider_id: str,
    model: str,
    dimensions: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Build a complete semantic view and atomically promote it.

    This function does not select or authorize a provider.  The supplied
    ``embedder`` is assumed to be an already-approved execution boundary.
    Failures are terminal and are never retried here.
    """
    paths = ProjectPaths.from_root(root)
    paths.index_root.mkdir(parents=True, exist_ok=True)
    database = _semantic_path(paths)
    before_snapshot = _database_snapshot(paths)
    semantic_input: _SemanticInput | None = None
    staging: Path | None = None
    effective_dimensions = dimensions or 0
    try:
        _validate_identity(provider_id, model, dimensions)
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not (
            1 <= batch_size <= MAX_BATCH_SIZE
        ):
            raise ValueError(f"batch_size must be in [1, {MAX_BATCH_SIZE}]")
        semantic_input = _load_semantic_input(paths.root)
        before = semantic_status(
            paths.root,
            provider_id=provider_id,
            model=model,
            dimensions=dimensions,
        )
        if before["status"] == "current":
            return {**before, "executed": False, "decision": "already_current"}

        if before.get("structural_current"):
            effective_dimensions = int(before["dimensions"])
            verification = verify_semantic_index(
                database,
                semantic_input,
                provider_id=provider_id,
                model=model,
                dimensions=effective_dimensions,
            )
            current_input = _load_semantic_input(paths.root)
            if current_input.input_fingerprint != semantic_input.input_fingerprint:
                raise RuntimeError("semantic input changed during health repair")
            retrieval = _retrieval_verification(
                paths,
                database,
                current_input,
                dimensions=effective_dimensions,
            )
            health_input = _load_semantic_input(paths.root)
            if health_input.input_fingerprint != current_input.input_fingerprint:
                raise RuntimeError("semantic input changed during health verification")
            health = _health_record(
                paths,
                health_input,
                provider_id=provider_id,
                model=model,
                dimensions=effective_dimensions,
                retrieval=retrieval,
            )
            _atomic_write_health(paths.semantic_health, health)
            return {
                **semantic_status(
                    paths.root,
                    provider_id=provider_id,
                    model=model,
                    dimensions=dimensions,
                ),
                "executed": False,
                "decision": "health_repaired",
                "verification": verification,
                "health": health,
            }

        documents = (*semantic_input.events, *semantic_input.passages)
        texts = tuple(document.text for document in documents)
        if texts:
            vectors, effective_dimensions = _embed_documents(
                embedder,
                texts,
                provider_id=provider_id,
                model=model,
                dimensions=dimensions,
                batch_size=batch_size,
            )
        else:
            # A fresh empty canonical ledger is a valid, queryable state.  It
            # must not trigger a local model load or a billable remote request.
            vectors = ()
            effective_dimensions = dimensions or 0

        descriptor, staging_name = tempfile.mkstemp(
            prefix=f".{database.name}.",
            suffix=".staging",
            dir=paths.index_root,
        )
        os.close(descriptor)
        staging = Path(staging_name)
        _write_staging(
            staging,
            semantic_input,
            vectors,
            provider_id=provider_id,
            model=model,
            dimensions=effective_dimensions,
        )
        current_input = _load_semantic_input(paths.root)
        if current_input.input_fingerprint != semantic_input.input_fingerprint:
            raise RuntimeError("semantic input changed during index build")
        verification = verify_semantic_index(
            staging,
            semantic_input,
            provider_id=provider_id,
            model=model,
            dimensions=effective_dimensions,
        )
        retrieval = _retrieval_verification(
            paths,
            staging,
            current_input,
            dimensions=effective_dimensions,
        )
        _sync_file(staging)
        os.replace(staging, database)
        _sync_directory(paths.index_root)
        health_input = _load_semantic_input(paths.root)
        if health_input.input_fingerprint != current_input.input_fingerprint:
            raise RuntimeError("semantic input changed during semantic index promotion")
        retrieval = _retrieval_verification(
            paths,
            database,
            health_input,
            dimensions=effective_dimensions,
        )
        health = _health_record(
            paths,
            health_input,
            provider_id=provider_id,
            model=model,
            dimensions=effective_dimensions,
            retrieval=retrieval,
        )
        _atomic_write_health(paths.semantic_health, health)
        return {
            "semantic_db": str(database),
            "status": "current",
            "stale": False,
            "executed": True,
            "decision": "bootstrapped" if before["status"] == "missing" else "refreshed",
            "provider_id": provider_id,
            "model": model,
            "dimensions": effective_dimensions,
            "current_fingerprint": semantic_input.input_fingerprint,
            "indexed_fingerprint": semantic_input.input_fingerprint,
            "verification": verification,
            "health": health,
        }
    except Exception as exc:
        failure_health = _failure_health_record(
            paths,
            semantic_input,
            before_snapshot,
            provider_id=provider_id,
            model=model,
            dimensions=effective_dimensions or dimensions,
            error=exc,
        )
        try:
            _atomic_write_health(paths.semantic_health, failure_health)
        except Exception as health_error:
            add_note = getattr(exc, "add_note", None)
            if callable(add_note):
                add_note(
                    "semantic health failure could not be recorded: "
                    f"{type(health_error).__name__}"
                )
        raise
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


__all__ = [
    "SemanticEmbedder",
    "ensure_semantic_index",
    "normalize_vector",
    "semantic_status",
    "verify_semantic_index",
]
