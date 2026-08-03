#!/usr/bin/env python3
"""Read-only audit report over canonical JSONL; never changes a ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.audit import audit_report
from core.ledger import read_jsonl


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-root", type=Path, required=True)
    args = parser.parse_args()
    records = [
        record
        for path in sorted((args.events_root / "daily").glob("*/events.jsonl"))
        for record in read_jsonl(path)
    ]
    print(json.dumps(audit_report(records), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
