"""Claude Desktop MCPB entry point for Universal Research Memory.

Thin launcher over the published package: initializes an empty store on
first run (local convenience only; canonical writes stay governed), then
starts the stdio server with the arguments Claude Desktop provides.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _ensure_initialized(argv: list[str]) -> None:
    if "--root" not in argv:
        return
    root = Path(argv[argv.index("--root") + 1]).expanduser()
    if not (root / "data" / "events").exists():
        from universal_research_mcp.indexing import initialize_project

        root.mkdir(parents=True, exist_ok=True)
        initialize_project(root)


def main() -> int:
    argv = sys.argv[1:]
    _ensure_initialized(argv)
    from universal_research_mcp.server import main as serve_main

    return serve_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
