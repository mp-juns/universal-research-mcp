#!/usr/bin/env python3
"""Validate or score provider-neutral MCP A/B benchmark JSONL records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.contracts import read_jsonl, validate_bundle
from benchmarks.scoring import paired_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--score", action="store_true")
    args = parser.parse_args()
    tasks, runs = read_jsonl(args.tasks), read_jsonl(args.runs)
    validate_bundle(tasks, runs, require_pairs=args.score)
    result = paired_report(tasks, runs) if args.score else {
        "schema_version": "benchmark-validation/1.0",
        "task_count": len(tasks),
        "run_count": len(runs),
        "valid": True,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
