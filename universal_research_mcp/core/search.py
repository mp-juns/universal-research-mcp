"""Pure candidate-search helpers shared by optional transport adapters."""

from __future__ import annotations

import re


def safe_fts_query(query: str) -> str:
    """Turn natural text into a conservative SQLite FTS5 OR query."""

    tokens = re.findall(r"[\w가-힣]+", query, flags=re.UNICODE)
    if not tokens:
        raise ValueError("query has no searchable tokens")
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
