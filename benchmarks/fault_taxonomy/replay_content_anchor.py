#!/usr/bin/env python3
"""Retrospective replay: content-anchored (passage-hash) verification over
the same evidence-link population the fault-taxonomy census measured.

Zero model tokens, strictly read-only on the reference project. For every
link the census could hash-check, this recomputes the verdict under the
content-anchored semantics proposed in docs/decisions/0004: a link passes
when the registered passage content (the cited line slice of the registered
revision; the whole registered file when no line range was registered) is
present byte-identically in the current file — in place first, then at any
displaced position. Only content that has genuinely changed or vanished
still alarms.

This upgrades the design consequence in results/REPORT.md from an
analytical projection to a measured replay on the identical corpus.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("census", HERE / "census.py")
census = importlib.util.module_from_spec(_spec)
sys.modules["census"] = census
_spec.loader.exec_module(census)


def slice_lines(blob: bytes, start: int | None, end: int | None) -> list[str] | None:
    lines = blob.decode("utf-8", errors="replace").splitlines()
    if not isinstance(start, int) or not isinstance(end, int):
        return lines  # whole-file citation: the passage is the whole file
    if start < 1 or end < start or end > len(lines):
        return None
    return lines[start - 1:end]


def present(passage: list[str], current: bytes, start: int | None) -> str:
    """'in_place' | 'displaced' | 'absent' for a passage in the current file."""
    cur = current.decode("utf-8", errors="replace").splitlines()
    n = len(passage)
    if n == 0 or all(not l.strip() for l in passage):
        return "absent"
    if isinstance(start, int) and cur[start - 1:start - 1 + n] == passage:
        return "in_place"
    for i in range(len(cur) - n + 1):
        if cur[i:i + n] == passage:
            return "in_place" if start is None and n == len(cur) else "displaced"
    return "absent"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=Path("research-events"))
    parser.add_argument("--out-summary", type=Path, required=True)
    args = parser.parse_args()
    ref = args.reference.resolve()
    cache: dict = {}

    counts: dict[str, int] = defaultdict(int)
    transitions: dict[str, int] = defaultdict(int)
    for day_file in sorted((ref / args.events / "daily").glob("*/events.jsonl")):
        for line in day_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            refs = census.event_refs(event)
            if not refs:
                counts["no_ref"] += 1
                continue
            for source in refs:
                path = source.get("source_path") or source.get("path") or ""
                want = (source.get("source_sha256") or source.get("sha256") or "").lower()
                start = source.get("line_start") or source.get("start")
                end = source.get("line_end") or source.get("end")
                if not path:
                    counts["no_ref"] += 1
                    continue
                if not want:
                    counts["ref_without_hash"] += 1
                    continue
                registered = census.blob_at(ref, path, want, cache)
                current_path = ref / path
                if registered is None:
                    counts["file_missing" if not current_path.is_file()
                           else "hash_unrecoverable"] += 1
                    continue
                passage = slice_lines(registered, start, end)
                if passage is None:
                    counts["range_invalid"] += 1
                    continue
                if not current_path.is_file():
                    counts["file_missing"] += 1
                    continue
                current = current_path.read_bytes()
                file_status = ("hash_match" if census.sha256(current) == want
                               else "hash_mismatch")
                where = present(passage, current, start if isinstance(start, int) else None)
                verdict = ("anchor_pass_" + where) if where != "absent" else "anchor_alarm"
                counts[verdict] += 1
                transitions[f"{file_status}->{verdict}"] += 1

    summary = {
        "semantics": "exact byte lines; in-place first, then displaced scan; "
                     "whole-file passage when no line range registered",
        "counts": dict(sorted(counts.items())),
        "file_hash_vs_anchor_transitions": dict(sorted(transitions.items())),
    }
    args.out_summary.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
