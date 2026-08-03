"""Export grounded research-search results as portable JSONL."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mcp_server import (
    _collect_lines,
    _extract_results,
    _normalize_result,
    _post,
)


def default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (
        Path.home()
        / "Downloads"
        / f"research-context-{timestamp}.jsonl"
    )


def build_source_record(
    query: str,
    item: dict[str, Any],
    rank: int,
    context_lines: int,
) -> dict[str, Any]:
    normalized = _normalize_result(item, rank)

    path = str(normalized.get("path") or "")
    start_line = int(normalized.get("start_line") or 1)
    end_line = int(
        normalized.get("end_line")
        or start_line
    )

    lines: list[dict[str, Any]] = []
    fetch_error: str | None = None

    if path:
        try:
            raw_fetch = _post(
                "/fetch",
                {
                    "path": path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "context_lines": context_lines,
                },
            )

            seen: set[tuple[int, str]] = set()

            _collect_lines(
                raw_fetch,
                lines,
                seen,
                max(1, start_line - context_lines),
            )

            lines.sort(
                key=lambda line: line["line_number"]
            )

        except Exception as exc:
            fetch_error = str(exc)

    content = "\n".join(
        f"{line['line_number']}: {line['text']}"
        for line in lines
    )

    return {
        "type": "source",
        "query": query,
        "rank": rank,
        "event_id": normalized.get("event_id"),
        "passage_id": normalized.get("passage_id"),
        "document_title": normalized.get(
            "document_title"
        ),
        "heading": normalized.get("heading"),
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "scores": normalized.get("scores"),
        "evidence_preview": normalized.get(
            "evidence_preview"
        ),
        "content": content,
        "lines": lines,
        "source_sha256": (
            item.get("source_sha256")
            or item.get("sha256")
        ),
        "fetch_error": fetch_error,
    }


def export_jsonl(
    query: str,
    output: Path,
    top_k: int,
    context_lines: int,
    mode: str,
) -> Path:
    search_payload: dict[str, Any] = {
        "query": query,
        "mode": mode,
        "top_k": top_k,
    }

    if mode == "hybrid":
        search_payload.update(
            {
                "lexical_weight": 0.25,
                "semantic_weight": 0.75,
            }
        )

    raw_search = _post("/search", search_payload)
    results = _extract_results(raw_search)[:top_k]

    exported_at = datetime.now(
        timezone.utc
    ).isoformat()

    metadata: dict[str, Any] = {}

    if isinstance(raw_search, dict):
        for key in (
            "query_expansion_count",
            "query_variants",
            "elapsed_ms",
            "mode",
        ):
            if key in raw_search:
                metadata[key] = raw_search[key]

    records: list[dict[str, Any]] = [
        {
            "type": "research_context_export",
            "schema_version": 1,
            "query": query,
            "exported_at": exported_at,
            "mode": mode,
            "requested_top_k": top_k,
            "result_count": len(results),
            "context_lines": context_lines,
            "search_metadata": metadata,
            "instructions_for_reader": [
                "Use only the source records in this file.",
                "Cite claims with path and line ranges.",
                "Distinguish recorded facts from interpretation.",
                "When records conflict, prefer the newer canonical record and report the conflict.",
            ],
        }
    ]

    records.extend(
        build_source_record(
            query=query,
            item=item,
            rank=rank,
            context_lines=context_lines,
        )
        for rank, item in enumerate(
            results,
            start=1,
        )
    )

    output = output.expanduser().resolve()
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = output.with_suffix(
        output.suffix + ".tmp"
    )

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
            )
            handle.write("\n")

        handle.flush()
        os.fsync(handle.fileno())

    temporary.replace(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Search the local Research Memory and export "
            "the matched original source lines as JSONL."
        )
    )

    parser.add_argument(
        "query",
        help="Natural-language research question.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--context-lines",
        type=int,
        default=15,
    )
    parser.add_argument(
        "--mode",
        choices=(
            "hybrid",
            "semantic",
            "lexical",
        ),
        default="hybrid",
    )

    args = parser.parse_args()

    output = args.output or default_output_path()

    result = export_jsonl(
        query=args.query.strip(),
        output=output,
        top_k=max(1, min(args.top_k, 20)),
        context_lines=max(
            0,
            min(args.context_lines, 50),
        ),
        mode=args.mode,
    )

    print(result)


if __name__ == "__main__":
    main()
