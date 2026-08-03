"""Local, read-only MCP for Universal Research Memory.

This server deliberately has no write, remote-proxy, model-loading, or local
LLM tools. Search results are candidates; callers must fetch evidence before
using a result for an important claim.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.search import safe_fts_query
from core.audit import audit_report
from core.ledger import read_jsonl


ROOT = Path(os.environ.get("UNIVERSAL_RESEARCH_ROOT", Path(__file__).resolve().parents[2])).resolve()
RESEARCH_DB = Path(os.environ.get("UNIVERSAL_RESEARCH_LEXICAL_DB", ROOT / "data/index/research.sqlite")).resolve()
EVENTS_ROOT = Path(os.environ.get("UNIVERSAL_RESEARCH_EVENTS_ROOT", ROOT / "data/events")).resolve()
MAX_FETCH_LINES = int(os.environ.get("UNIVERSAL_RESEARCH_MAX_FETCH_LINES", "500"))

DENIED_BASENAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "authorized_keys", "credentials.json"}
DENIED_FRAGMENTS = {"secret", "token", "credential", "private_key", "api_key", "apikey"}

INSTRUCTIONS = """
Use memory_search_candidates to find candidate research records. A search score
is not evidence. Before making an important claim, call memory_fetch_evidence
with the exact path and line range returned by search, then report that source
range and its hash. This server is read-only and cannot approve, write, amend,
or execute research work.
""".strip()

mcp = FastMCP("Universal Research Memory", instructions=INSTRUCTIONS)


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError(f"derived lexical index not found: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def resolve_safe_path(relative_path: str) -> Path:
    supplied = Path(relative_path)
    if supplied.is_absolute() or ".." in supplied.parts:
        raise ValueError("unsafe path")
    lowered = [part.lower() for part in supplied.parts]
    if supplied.name.lower() in DENIED_BASENAMES or any(fragment in part for part in lowered for fragment in DENIED_FRAGMENTS):
        raise ValueError("access to this path is denied")
    resolved = (ROOT / supplied).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("path escapes project root") from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"source artifact not found: {relative_path}")
    return resolved


def _result(row: sqlite3.Row, rank: int, score: float | None = None) -> dict[str, Any]:
    return {
        "rank": rank,
        "candidate_only": True,
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "status": row["status"],
        "date": row["date"],
        "summary": row["summary"],
        "path": row["source_path"],
        "heading": row["source_heading"],
        "start_line": row["line_start"],
        "end_line": row["line_end"],
        "source_sha256": row["source_sha256"],
        "lexical_score": score,
    }


def search_lexical(query: str, top_k: int, status: str | None = None) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = [safe_fts_query(query)]
    if status:
        filters.append("e.status = ?")
        params.append(status)
    params.append(top_k)
    where = " AND " + " AND ".join(filters) if filters else ""
    with closing(open_readonly(RESEARCH_DB)) as db:
        rows = db.execute(
            f"""
            SELECT e.event_id, e.event_type, e.status, e.date, e.summary,
                   e.source_path, e.source_heading, e.line_start, e.line_end,
                   e.source_sha256, bm25(event_fts) AS bm25_raw
            FROM event_fts JOIN events e ON e.event_id = event_fts.event_id
            WHERE event_fts MATCH ? {where}
            ORDER BY bm25_raw ASC LIMIT ?
            """,
            params,
        ).fetchall()
    return [_result(row, rank, -float(row["bm25_raw"])) for rank, row in enumerate(rows, 1)]


def indexed_source_hashes(path: str) -> list[str]:
    """Return distinct nonempty hashes recorded for a source path in the index."""

    with closing(open_readonly(RESEARCH_DB)) as db:
        rows = db.execute(
            "SELECT DISTINCT source_sha256 FROM events WHERE source_path = ? AND source_sha256 IS NOT NULL AND source_sha256 <> ''",
            (path,),
        ).fetchall()
    return sorted(str(row["source_sha256"]) for row in rows)


def _recency_key(row: sqlite3.Row) -> float:
    value = row["date"]
    try:
        raw = json.loads(row["raw_json"] or "{}")
        value = raw.get("timestamp_end") or raw.get("timestamp_start") or value
    except json.JSONDecodeError:
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


@mcp.tool()
def memory_search_candidates(query: str, top_k: int = 8, mode: Literal["lexical", "hybrid", "semantic"] = "lexical", status: str | None = None) -> dict[str, Any]:
    """Return lexical search candidates. Fetch original evidence before concluding."""

    if mode != "lexical":
        return {
            "query": query,
            "mode": mode,
            "candidate_only": True,
            "results": [],
            "notice": "This local core adapter intentionally exposes lexical retrieval only. Configure a derived semantic adapter separately; similarity remains candidate metadata.",
        }
    top_k = max(1, min(int(top_k), 100))
    return {"query": query, "mode": "lexical", "candidate_only": True, "results": search_lexical(query, top_k, status)}


@mcp.tool()
def memory_latest(top_k: int = 5) -> dict[str, Any]:
    """Return latest non-reference records, ordered by recorded event time."""

    with closing(open_readonly(RESEARCH_DB)) as db:
        rows = db.execute("SELECT event_id, event_type, status, date, summary, raw_json FROM events WHERE event_type <> 'reference_document'").fetchall()
    ordered = sorted(rows, key=lambda row: (_recency_key(row), row["event_id"]), reverse=True)[: max(1, min(int(top_k), 100))]
    return {"results": [{key: row[key] for key in ("event_id", "event_type", "status", "date", "summary")} for row in ordered]}


@mcp.tool()
def memory_fetch_evidence(path: str, start_line: int, end_line: int | None = None, context_lines: int = 8) -> dict[str, Any]:
    """Fetch line-addressable evidence and its content hash from a safe local artifact."""

    resolved = resolve_safe_path(path)
    start = max(1, int(start_line) - max(0, min(int(context_lines), 50)))
    requested_end = int(end_line) if end_line is not None else int(start_line) + 40
    end = requested_end + max(0, min(int(context_lines), 50))
    if end < start or end - start + 1 > MAX_FETCH_LINES:
        raise ValueError("invalid or excessive fetch range")
    lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    end = min(end, len(lines))
    content = "\n".join(f"{number}: {text}" for number, text in enumerate(lines[start - 1:end], start))
    current_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
    hashes = indexed_source_hashes(path)
    indexed_sha256 = hashes[0] if len(hashes) == 1 else None
    integrity_status = (
        "matched" if indexed_sha256 == current_sha256 else "mismatched"
        if indexed_sha256 is not None else "not_indexed" if not hashes else "ambiguous"
    )
    return {
        "path": str(resolved.relative_to(ROOT)),
        "start_line": start,
        "end_line": end,
        # Keep the prior field as a compatibility alias. New callers should
        # use the explicit indexed/current fields and integrity status.
        "sha256": current_sha256,
        "indexed_sha256": indexed_sha256,
        "current_sha256": current_sha256,
        "integrity_status": integrity_status,
        "content": content,
    }


@mcp.tool()
def memory_audit_ledger() -> dict[str, Any]:
    """Return read-only policy and record-integrity findings for canonical JSONL."""

    records = [
        record
        for event_path in sorted((EVENTS_ROOT / "daily").glob("*/events.jsonl"))
        for record in read_jsonl(event_path)
    ]
    return audit_report(records)


@mcp.tool()
def research_search(query: str, top_k: int = 8, mode: Literal["lexical", "hybrid", "semantic"] = "lexical", status: str | None = None) -> dict[str, Any]:
    """Compatibility alias for memory_search_candidates."""

    return memory_search_candidates(query=query, top_k=top_k, mode=mode, status=status)


@mcp.tool()
def research_latest(top_k: int = 5) -> dict[str, Any]:
    """Compatibility alias for memory_latest."""

    return memory_latest(top_k=top_k)


@mcp.tool()
def research_fetch(path: str, start_line: int, end_line: int | None = None, context_lines: int = 8) -> dict[str, Any]:
    """Compatibility alias for memory_fetch_evidence."""

    return memory_fetch_evidence(path=path, start_line=start_line, end_line=end_line, context_lines=context_lines)


if __name__ == "__main__":
    mcp.run()
