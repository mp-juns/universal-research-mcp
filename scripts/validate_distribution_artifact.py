#!/usr/bin/env python3
"""Repository-only launcher for the packaged wheel validator."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universal_research_mcp.tools.distribution import main


if __name__ == "__main__":
    raise SystemExit(main())
