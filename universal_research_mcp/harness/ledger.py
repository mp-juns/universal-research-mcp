"""Minimal append-only internal harness ledger sink."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any

from universal_research_mcp.runtime import ProjectPaths


class AppendOnlyJsonlSink:
    """Append one compact JSON object per line without rewriting history."""

    def __init__(self, root: str | Path, relative_path: str = "data/governance/harness.jsonl") -> None:
        self.path = ProjectPaths.from_root(root).resolve_relative(relative_path)
        self._lock = threading.Lock()

    def __call__(self, record: dict[str, Any]) -> bool:
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return True
