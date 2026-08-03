"""Backward-compatible source-tree launcher for Universal Research Memory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universal_research_mcp.server import (  # noqa: E402,F401
    EVENTS_ROOT,
    MAX_FETCH_LINES,
    RESEARCH_DB,
    ROOT as RESEARCH_ROOT,
    configure_runtime,
    indexed_source_hashes,
    main,
    mcp,
    memory_audit_ledger,
    memory_fetch_evidence,
    memory_latest,
    memory_search_candidates,
    open_readonly,
    research_fetch,
    research_latest,
    research_search,
    resolve_safe_path,
    search_lexical,
)

configure_runtime()


if __name__ == "__main__":
    raise SystemExit(main())
