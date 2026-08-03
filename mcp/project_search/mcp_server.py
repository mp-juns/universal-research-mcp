"""Read-only MCP proxy for the Project Research Search daemon.

This process does not load an embedding model or local LLM.
It forwards Codex tool calls to the existing FastAPI daemon.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import sqlite3
from mcp.server.fastmcp import FastMCP
PROJECT_ROOT = Path(
    os.environ.get(
        "PROJECT_SEARCH_ROOT",
        Path(__file__).resolve().parents[2],
    )
).resolve()

RESEARCH_DB = Path(
    os.environ.get(
        "PROJECT_SEARCH_RESEARCH_DB",
        PROJECT_ROOT / "data/index/research.sqlite",
    )
).resolve()

API_BASE = os.environ.get(
    "PROJECT_SEARCH_API_URL",
    "http://127.0.0.1:8765",
).rstrip("/")

API_KEY_FILE = Path(
    os.environ.get(
        "PROJECT_SEARCH_API_KEY_FILE",
        "~/.project-search-api-key",
    )
).expanduser()

REQUEST_TIMEOUT = float(
    os.environ.get("PROJECT_SEARCH_MCP_TIMEOUT", "90")
)

SERVER_INSTRUCTIONS = """
For questions about previous experiments, numerical results, failure causes,
research decisions, implementation history, or research details,
call research_search before answering.

For questions asking about the latest, newest, most recent, current status,
recent progress, or what was done recently,
call research_latest first to retrieve chronologically ordered records.
Do not use semantic search to determine recency.

For important claims, call research_fetch to inspect the original source lines.
Prefer canonical SUMMARY.md and DECISION.md records. When records conflict,
report the conflict and favor the newer canonical record rather than silently
merging them.

Return path and line ranges with conclusions. This server is read-only.
Do not ask another local LLM to summarize these results; inspect the retrieved
evidence directly.
""".strip()


mcp = FastMCP(
    "Research Memory",
    instructions=SERVER_INSTRUCTIONS,
)


def _api_key() -> str:
    env_key = os.environ.get("PROJECT_SEARCH_API_KEY", "").strip()
    if env_key:
        return env_key

    try:
        return API_KEY_FILE.read_text(
            encoding="utf-8"
        ).strip()
    except OSError as exc:
        raise RuntimeError(
            f"Project Search API key not found: {API_KEY_FILE}"
        ) from exc


def _decode_response(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()

    if not text:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # /fetch 등에서 NDJSON을 반환하는 경우.
    records: list[Any] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            return {"raw": text}

    return records


def _post(path: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    request = Request(
        f"{API_BASE}{path}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json, application/x-ndjson",
        },
    )

    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            return _decode_response(response.read())

    except HTTPError as exc:
        detail = exc.read().decode(
            "utf-8",
            errors="replace",
        )
        raise RuntimeError(
            f"Project Search API returned HTTP {exc.code}: {detail}"
        ) from exc

    except URLError as exc:
        raise RuntimeError(
            f"Cannot reach Project Search API at {API_BASE}: "
            f"{exc.reason}"
        ) from exc


def _first(
    item: dict[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    for key in keys:
        value = item.get(key)

        if value is not None and value != "":
            return value

    return default


def _clip(value: Any, limit: int = 2600) -> str:
    text = str(value or "").strip()

    if len(text) <= limit:
        return text

    return text[:limit] + "\n…[truncated]"


def _looks_like_result(item: Any) -> bool:
    if not isinstance(item, dict):
        return False

    result_keys = {
        "event_id",
        "passage_id",
        "path",
        "source_path",
        "heading",
        "start_line",
        "hybrid_score",
        "semantic_score",
        "lexical_score",
    }

    return bool(result_keys.intersection(item))


def _extract_results(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        direct = [
            item
            for item in value
            if _looks_like_result(item)
        ]

        if direct:
            return direct

        for item in value:
            nested = _extract_results(item)
            if nested:
                return nested

        return []

    if not isinstance(value, dict):
        return []

    for key in (
        "results",
        "items",
        "hits",
        "matches",
        "documents",
        "data",
        "payload",
    ):
        nested = value.get(key)

        if nested is None:
            continue

        found = _extract_results(nested)

        if found:
            return found

    if _looks_like_result(value):
        return [value]

    return []


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest-ranked passage for each event."""
    results: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in candidates:
        event_id = _first(item, "event_id", "id")
        key = (
            ("event", str(event_id))
            if event_id is not None
            else (
                "passage",
                _first(item, "path", "source_path", "file_path"),
                _first(item, "start_line", "line_start"),
                _first(item, "end_line", "line_end"),
                _first(item, "passage_id", "chunk_id"),
            )
        )
        if key in seen:
            continue
        seen.add(key)
        results.append(item)
    return results


def _normalize_result(
    item: dict[str, Any],
    rank: int,
) -> dict[str, Any]:
    return {
        "rank": rank,
        "event_id": _first(
            item,
            "event_id",
            "id",
        ),
        "passage_id": _first(
            item,
            "passage_id",
            "chunk_id",
        ),
        "document_title": _first(
            item,
            "document_title",
            "title",
        ),
        "heading": _first(
            item,
            "heading",
            "section_heading",
            "summary",
        ),
        "path": _first(
            item,
            "path",
            "source_path",
            "file_path",
        ),
        "start_line": _first(
            item,
            "start_line",
            "line_start",
        ),
        "end_line": _first(
            item,
            "end_line",
            "line_end",
        ),
        "evidence_preview": _clip(
            _first(
                item,
                "evidence",
                "passage",
                "snippet",
                "preview",
                "content",
                "text",
                "summary",
                default="",
            )
        ),
        "scores": {
            "hybrid": item.get("hybrid_score"),
            "semantic": item.get("semantic_score"),
            "lexical": item.get("lexical_score"),
        },
    }


def _add_line(
    output: list[dict[str, Any]],
    seen: set[tuple[int, str]],
    number: Any,
    text: Any,
) -> None:
    try:
        line_number = int(number)
    except (TypeError, ValueError):
        line_number = len(output) + 1

    line_text = str(text or "")
    key = (line_number, line_text)

    if key in seen:
        return

    seen.add(key)
    output.append(
        {
            "line_number": line_number,
            "text": line_text,
        }
    )


def _collect_lines(
    value: Any,
    output: list[dict[str, Any]],
    seen: set[tuple[int, str]],
    default_start: int,
) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                _add_line(
                    output,
                    seen,
                    default_start + index,
                    item,
                )
            else:
                _collect_lines(
                    item,
                    output,
                    seen,
                    default_start + index,
                )

        return

    if not isinstance(value, dict):
        return

    record_start = _first(
        value,
        "start_line",
        "line_start",
        "start",
        default=default_start,
    )

    for key in (
        "lines",
        "selected_lines",
        "items",
    ):
        candidate = value.get(key)

        if isinstance(candidate, list):
            _collect_lines(
                candidate,
                output,
                seen,
                int(record_start),
            )
            return

    has_line_number = any(
        key in value
        for key in (
            "line_number",
            "line_no",
            "number",
            "line",
        )
    )

    if has_line_number:
        _add_line(
            output,
            seen,
            _first(
                value,
                "line_number",
                "line_no",
                "number",
                "line",
                default=record_start,
            ),
            _first(
                value,
                "content",
                "text",
                "line_text",
                "value",
                default="",
            ),
        )
        return

    for key in ("content", "text"):
        candidate = value.get(key)

        if isinstance(candidate, str):
            for index, line in enumerate(
                candidate.splitlines()
            ):
                _add_line(
                    output,
                    seen,
                    int(record_start) + index,
                    line,
                )
            return

    for key in ("data", "payload", "result"):
        nested = value.get(key)

        if nested is not None:
            _collect_lines(
                nested,
                output,
                seen,
                int(record_start),
            )


@mcp.tool()
def research_search(
    query: str,
    top_k: int = 8,
    mode: Literal[
        "hybrid",
        "semantic",
        "lexical",
    ] = "hybrid",
    path_contains: str | None = None,
) -> dict[str, Any]:
    """Search canonical research records.

    Use this before answering questions about previous experiments, metrics,
    decisions, failure causes, implementation history, or the current state.

    Args:
        query: Natural-language or project-specific search query.
        top_k: Number of diverse results to return, from 1 to 20.
        mode: Hybrid is normally best. Semantic handles paraphrases, while
            lexical is useful for exact experiment names and identifiers.
        path_contains: Optional case-insensitive substring filter on paths.
    """

    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")

    top_k = max(1, min(int(top_k), 20))

    payload: dict[str, Any] = {
        "query": query,
        "mode": mode,
        "top_k": min(top_k * 4, 80),
    }

    if mode == "hybrid":
        payload.update(
            {
                "lexical_weight": 0.25,
                "semantic_weight": 0.75,
            }
        )

    raw = _post("/search", payload)
    candidates = _extract_results(raw)

    if path_contains:
        needle = path_contains.casefold()

        candidates = [
            item
            for item in candidates
            if needle
            in str(
                _first(
                    item,
                    "path",
                    "source_path",
                    "file_path",
                    default="",
                )
            ).casefold()
        ]

    candidates = _dedupe_candidates(candidates)
    results = [
        _normalize_result(item, rank)
        for rank, item in enumerate(
            candidates[:top_k],
            start=1,
        )
    ]

    metadata: dict[str, Any] = {}

    if isinstance(raw, dict):
        for key in (
            "query_expansion_count",
            "query_variants",
            "elapsed_ms",
            "mode",
        ):
            if key in raw:
                metadata[key] = raw[key]

    return {
        "query": query,
        "mode": mode,
        "count": len(results),
        "metadata": metadata,
        "results": results,
    }

def _event_recency_timestamp(raw_json: str | None, date: str | None) -> str:
    """Pick the best available event timestamp for chronological ordering.

    Prefer timestamp_end, then timestamp_start, then the day-level date.
    Missing fields fall back without rewriting stored events.
    """

    payload: dict[str, Any] = {}
    if raw_json:
        try:
            loaded = json.loads(raw_json)
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {}
    for key in ("timestamp_end", "timestamp_start"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(date, str) and date.strip():
        return date.strip()
    return ""


def _event_recency_sort_key(raw_json: str | None, date: str | None) -> float:
    """Normalize mixed ISO-8601 offsets to one absolute UTC ordering key."""

    value = _event_recency_timestamp(raw_json, date)
    if not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def list_latest_research_events(
    db: sqlite3.Connection,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Return latest research events ordered by event timestamp, then event_id."""

    limit = max(1, int(top_k))
    rows = db.execute(
        """
        SELECT
            event_id,
            date,
            event_type,
            status,
            summary,
            raw_json
        FROM events
        WHERE event_type <> 'reference_document'
        """
    ).fetchall()
    rows = sorted(
        rows,
        key=lambda row: (
            _event_recency_sort_key(row["raw_json"], row["date"]),
            row["event_id"],
        ),
        reverse=True,
    )[:limit]
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("raw_json", None)
        results.append(item)
    return results


@mcp.tool()
def research_latest(top_k: int = 5) -> dict[str, Any]:
    """Return latest research events ordered by timestamp_end when present."""

    with sqlite3.connect(RESEARCH_DB) as db:
        db.row_factory = sqlite3.Row
        results = list_latest_research_events(db, top_k=top_k)

    return {"results": results}

@mcp.tool()
def research_fetch(
    path: str,
    start_line: int,
    end_line: int | None = None,
    context_lines: int = 8,
) -> dict[str, Any]:
    """Fetch original source lines for an important search result.

    Call this after research_search when a conclusion depends on the exact
    wording, number, date, result status, or experiment conditions.

    Args:
        path: Exact path returned by research_search.
        start_line: First relevant line returned by research_search.
        end_line: Last relevant line. Defaults to start_line plus 40.
        context_lines: Additional surrounding lines, from 0 to 50.
    """

    path = path.strip()
    if not path:
        raise ValueError("path must not be empty")

    start_line = max(1, int(start_line))
    resolved_end = (
        max(start_line, int(end_line))
        if end_line is not None
        else start_line + 40
    )
    context_lines = max(0, min(int(context_lines), 50))

    raw = _post(
        "/fetch",
        {
            "path": path,
            "start_line": start_line,
            "end_line": resolved_end,
            "context_lines": context_lines,
        },
    )

    lines: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    _collect_lines(
        raw,
        lines,
        seen,
        max(1, start_line - context_lines),
    )

    lines.sort(key=lambda item: item["line_number"])

    content = "\n".join(
        f"{item['line_number']}: {item['text']}"
        for item in lines
    )

    return {
        "path": path,
        "requested_start_line": start_line,
        "requested_end_line": resolved_end,
        "context_lines": context_lines,
        "line_count": len(lines),
        "content": content,
        "lines": lines,
    }


if __name__ == "__main__":
    # FastMCP uses STDIO by default, which is what local Codex expects.
    mcp.run()
