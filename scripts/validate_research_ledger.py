#!/usr/bin/env python3
"""Validate canonical JSONL records without writing files or indexes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from universal_research_mcp.core.ledger import read_jsonl, validate_records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-root", type=Path, required=True)
    args = parser.parse_args()

    event_paths = sorted((args.events_root / "daily").glob("*/events.jsonl"))
    records = [record for path in event_paths for record in read_jsonl(path)]
    issues = validate_records(records)
    report = {
        "events_root": str(args.events_root),
        "record_count": len(records),
        "valid": not issues,
        "issues": [issue.__dict__ for issue in issues],
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
