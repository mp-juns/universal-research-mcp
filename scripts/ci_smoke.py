#!/usr/bin/env python3
"""Platform-aware fresh-wheel smoke helper used only by repository CI."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


def _entrypoint(venv: Path, name: str) -> Path:
    directory = venv / ("Scripts" if sys.platform == "win32" else "bin")
    suffix = ".exe" if sys.platform == "win32" else ""
    return directory / f"{name}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        venv = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = _entrypoint(venv, "python")
        subprocess.run([str(python), "-m", "pip", "install", "--force-reinstall", str(args.wheel)], check=True)
        research = _entrypoint(venv, "universal-research")
        subprocess.run([str(research), "--version"], check=True)
        subprocess.run([str(research), "init", str(root / "research")], check=True)
        subprocess.run([str(research), "doctor", "--root", str(root / "research")], check=True)
        probe = "import universal_research_mcp.core; import universal_research_mcp.governance"
        subprocess.run([str(python), "-c", probe], check=True)
        old = subprocess.run([str(python), "-c", "import core"], check=False)
        if old.returncode == 0:
            raise RuntimeError("legacy top-level core import unexpectedly succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
