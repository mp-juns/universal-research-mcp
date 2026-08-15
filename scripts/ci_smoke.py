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
    wheel = args.wheel
    if not wheel.is_file():
        matches = sorted(wheel.parent.glob(wheel.name))
        if len(matches) != 1:
            raise FileNotFoundError(f"expected exactly one wheel matching: {wheel}")
        wheel = matches[0]
    wheel = wheel.resolve()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        venv = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, cwd=root)
        python = _entrypoint(venv, "python")
        subprocess.run(
            [str(python), "-m", "pip", "install", "--force-reinstall", str(wheel)],
            check=True,
            cwd=root,
        )
        research = _entrypoint(venv, "universal-research")
        subprocess.run([str(research), "--version"], check=True, cwd=root)
        subprocess.run([str(research), "init", str(root / "research")], check=True, cwd=root)
        subprocess.run(
            [str(research), "doctor", "--root", str(root / "research")],
            check=True,
            cwd=root,
        )
        probe = (
            "import importlib.util; "
            "import universal_research_mcp.core; import universal_research_mcp.governance; "
            "assert importlib.util.find_spec('universal_research_mcp.providers') is None; "
            "assert importlib.util.find_spec('universal_research_mcp.agent_runtime') is None; "
            "assert importlib.util.find_spec('universal_research_mcp.harness') is None"
        )
        subprocess.run([str(python), "-c", probe], check=True, cwd=root)
        old = subprocess.run([str(python), "-c", "import core"], check=False, cwd=root)
        if old.returncode == 0:
            raise RuntimeError("legacy top-level core import unexpectedly succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
