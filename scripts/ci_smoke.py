#!/usr/bin/env python3
"""Platform-aware fresh-wheel smoke helper used only by repository CI."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


BOUNDARY_TESTS = (
    "tests/test_memory_claim_gate.py",
    "tests/test_semantic_setup.py",
    "tests/test_semantic_runtime.py",
    *(f"tests/test_mcp_ingest.py::{name}" for name in (
        "test_ingest_rejects_parent_links_before_writing",
        "test_ingest_rejects_linked_metadata_files",
        "test_ingest_rejects_a_parent_swapped_after_path_validation",
        "test_portable_path_rechecks_parent_links",
        "test_ingest_rejects_windows_junction",
        "test_cli_and_mcp_writers_share_a_process_lock_across_validation",
        "test_admin_append_fsyncs_staging_and_preserves_original_on_replace_failure",
        "test_admin_writes_reject_linked_canonical_paths",
    )),
)


def _entrypoint(venv: Path, name: str) -> Path:
    directory = venv / ("Scripts" if sys.platform == "win32" else "bin")
    suffix = ".exe" if sys.platform == "win32" else ""
    return directory / f"{name}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument(
        "--boundary-tests", action="store_true",
        help="Exercise evidence, canonical-write and model-snapshot regressions against the installed wheel.",
    )
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
            [str(python), "-m", "pip", "install", "--force-reinstall",
             str(wheel) + ("[test]" if args.boundary_tests else "")],
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
        if args.boundary_tests:
            # Copy tests alone: running them in the checkout could import source
            # instead of the wheel whose platform compatibility we are checking.
            source_root = Path(__file__).resolve().parents[1]
            (root / "tests").mkdir()
            for relative in sorted({node.split("::", 1)[0] for node in BOUNDARY_TESTS}):
                shutil.copyfile(source_root / relative, root / relative)
            subprocess.run(
                [str(python), "-m", "pytest", "-q", "-rs",
                 "--basetemp", str(root / "pytest-temp"), *BOUNDARY_TESTS],
                check=True, cwd=root,
            )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
