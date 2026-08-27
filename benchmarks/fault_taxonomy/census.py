#!/usr/bin/env python3
"""Stage A machine census for the fault-taxonomy protocol (zero model tokens).

Reads the reference project strictly read-only. One CSV row per evidence
link ((record, source_ref) pair) with two independent status columns and a
machine mismatch triage; summary JSON reports rates by registration-date
tertile (cit_freshqa2024_change_rate_strata: stratify by rate of change so
survivorship in the recent tertile stays visible).
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path

STATUSES = ("no_ref", "ref_without_hash", "file_missing", "hash_unrecoverable",
            "range_invalid", "hash_mismatch", "hash_match")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def event_refs(event: dict) -> list[dict]:
    sources = [event.get("source")] if isinstance(event.get("source"), dict) else []
    sources += [s for s in (event.get("sources") or []) if isinstance(s, dict)]
    return sources


def blob_at(reference: Path, path: str, want: str, cache: dict) -> bytes | None:
    key = (path, want)
    if key in cache:
        return cache[key]
    found = None
    current = reference / path
    if current.is_file() and sha256(current.read_bytes()) == want:
        found = current.read_bytes()
    else:
        revs = subprocess.run(["git", "rev-list", "--all", "--", path],
                              capture_output=True, text=True, cwd=reference).stdout.split()
        for rev in revs:
            data = subprocess.run(["git", "show", f"{rev}:{path}"],
                                  capture_output=True, cwd=reference).stdout
            if sha256(data) == want:
                found = data
                break
    cache[key] = found
    return found


def diff_changed_lines(old: bytes, new: bytes) -> set[int]:
    """1-indexed line numbers of the OLD revision touched by the change."""
    old_lines = old.decode("utf-8", errors="replace").splitlines()
    new_lines = new.decode("utf-8", errors="replace").splitlines()
    changed: set[int] = set()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag != "equal":
            changed.update(range(i1 + 1, max(i2, i1 + 1) + 1))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=Path("research-events"))
    parser.add_argument("--out-csv", type=Path, required=True)
    parser.add_argument("--out-summary", type=Path, required=True)
    args = parser.parse_args()
    ref = args.reference.resolve()
    cache: dict = {}
    registry = set()
    reg_file = ref / args.events / "sources.jsonl"
    if reg_file.is_file():
        for line in reg_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                registry.add((d.get("source_path"), (d.get("source_sha256") or "").lower()))

    rows = []
    dates = []
    for day_file in sorted((ref / args.events / "daily").glob("*/events.jsonl")):
        day = day_file.parent.name
        for line in day_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            refs = event_refs(event)
            base = {"event_id": event.get("event_id"), "date": day,
                    "event_type": event.get("event_type") or event.get("record_kind")}
            if not refs:
                rows.append({**base, "path": "", "link_hash_status": "no_ref",
                             "registry_status": "n/a", "evaluated_against": "",
                             "machine_triage": "", "diff_overlap_lines": ""})
                dates.append(day)
                continue
            for source in refs:
                path = source.get("source_path") or source.get("path") or ""
                want = (source.get("source_sha256") or source.get("sha256") or "").lower()
                start = source.get("line_start") or source.get("start")
                end = source.get("line_end") or source.get("end")
                row = {**base, "path": path, "start": start, "end": end,
                       "registry_status": "registered" if (path, want) in registry else "unregistered",
                       "machine_triage": "", "diff_overlap_lines": "", "evaluated_against": ""}
                if not path:
                    row["link_hash_status"] = "no_ref"
                elif not want:
                    row["link_hash_status"] = "ref_without_hash"
                else:
                    registered = blob_at(ref, path, want, cache)
                    current = (ref / path)
                    if registered is None:
                        row["link_hash_status"] = ("hash_unrecoverable"
                                                   if current.is_file() or True else "file_missing")
                        if not current.is_file():
                            row["link_hash_status"] = "file_missing"
                    else:
                        basis = registered
                        row["evaluated_against"] = "registered_revision"
                        n_lines = len(basis.decode("utf-8", errors="replace").splitlines())
                        if isinstance(start, int) and (start < 1 or (isinstance(end, int) and end > n_lines)):
                            row["link_hash_status"] = "range_invalid"
                        elif not current.is_file():
                            row["link_hash_status"] = "file_missing"
                        elif sha256(current.read_bytes()) == want:
                            row["link_hash_status"] = "hash_match"
                        else:
                            row["link_hash_status"] = "hash_mismatch"
                            changed = diff_changed_lines(registered, current.read_bytes())
                            if isinstance(start, int) and isinstance(end, int):
                                overlap = sorted(set(range(start, end + 1)) & changed)
                                row["diff_overlap_lines"] = ";".join(map(str, overlap[:20]))
                                row["machine_triage"] = ("C1_candidate" if not overlap
                                                          else "human_C2_C3")
                            else:
                                row["machine_triage"] = "human_C2_C3"
                rows.append(row)
                dates.append(day)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({k for r in rows for k in r}))
        writer.writeheader()
        writer.writerows(rows)

    ordered_dates = sorted(dates)
    cut1 = ordered_dates[len(ordered_dates) // 3]
    cut2 = ordered_dates[2 * len(ordered_dates) // 3]
    def tertile(day: str) -> str:
        return "T1" if day <= cut1 else ("T2" if day <= cut2 else "T3")
    summary: dict = {"total_links": len(rows), "tertile_cuts": [cut1, cut2],
                     "status_counts": defaultdict(int), "by_tertile": defaultdict(lambda: defaultdict(int)),
                     "machine_triage_counts": defaultdict(int)}
    for row in rows:
        summary["status_counts"][row["link_hash_status"]] += 1
        summary["by_tertile"][tertile(row["date"])][row["link_hash_status"]] += 1
        if row["machine_triage"]:
            summary["machine_triage_counts"][row["machine_triage"]] += 1
    summary = json.loads(json.dumps(summary))
    args.out_summary.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"links": len(rows), "statuses": summary["status_counts"],
                      "machine_triage": summary["machine_triage_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
