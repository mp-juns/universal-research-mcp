#!/usr/bin/env python3
"""Build isolated development fixtures for the claim-gating benchmark."""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.contracts import read_jsonl
from benchmarks.integrity_claim_gate import validate_bundle
from benchmarks.integrity_fixtures import build_development_fixtures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tasks = read_jsonl(args.tasks)
    validate_bundle(tasks, [])
    build_development_fixtures(tasks, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
