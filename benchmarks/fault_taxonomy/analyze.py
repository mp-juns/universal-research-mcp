#!/usr/bin/env python3
"""Preregistered analysis for the fault-taxonomy protocol.

Constants carry their literature grounds as citation IDs from
real-corpus-fault-taxonomy-v1.citations.json (manifest sha256 27c280a2...).
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

# cit_alce2023_kappa_acceptance: a peer-reviewed benchmark accepted kappa
# 0.525-0.698 as validity evidence; cit_landiskoch1977_bands: 0.61-0.80 is
# the conventional "substantial" band (arbitrariness disclosed in protocol).
KAPPA_THRESHOLD = 0.6

# cit_liu2023_verifiability_rates: deployed systems left ~48.5% of sentences
# unsupported; the gate must address a substantial minority of real failures
# to repay its standing cost (~1.4x uncached tokens, 16.7% false blocks).
# Fixed ex ante in the style of a coverage constraint
# (cit_selectivenet2019_coverage_constraint).
ADDRESSABLE_BASELINE_D1 = 0.30

# cit_acl2026_no_power_analysis: the field reports a median of 170 items and
# "no papers use power analysis to determine sample size"; our n is justified
# by Wilson-CI width instead (see wilson_ci docstring).
STAGE_B_N = 50
STAGE_B_EXTENSION = 30           # applied once iff blind-spot events < 5
STAGE_B_MIN_EVENTS = 5


def wilson_ci(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% Wilson interval. At an assumed 10% blind-spot rate: n=50 gives
    [4.4%, 21.4%], n=80 gives [5.3%, 18.2%] - tight enough to test the 30%
    decision line, which is the preregistered justification for n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def cohen_kappa(a: list[str], b: list[str]) -> float:
    assert len(a) == len(b) and a
    agree = sum(1 for x, y in zip(a, b) if x == y) / len(a)
    codes = set(a) | set(b)
    expected = sum((a.count(c) / len(a)) * (b.count(c) / len(b)) for c in codes)
    return (agree - expected) / (1 - expected) if expected < 1 else 1.0


def addressable(match_n: int, mismatch_n: int, blindspot_rate: float,
                substantive_rate: float) -> float:
    caught = mismatch_n * substantive_rate
    missed = match_n * blindspot_rate
    return caught / (caught + missed) if caught + missed else 0.0


def cluster_bootstrap(rows: list[dict], stat, cluster_key: str = "event_id",
                      iterations: int = 10000, seed: int = 20260828) -> tuple[float, float]:
    """Record-cluster bootstrap: links within one record resample together
    (naive link-level resampling understates CI width on this corpus, where
    a few source files dominate the link population)."""
    rng = random.Random(seed)
    clusters = defaultdict(list)
    for row in rows:
        clusters[row[cluster_key]].append(row)
    keys = sorted(clusters)
    values = []
    for _ in range(iterations):
        sample = [row for key in (rng.choice(keys) for _ in keys) for row in clusters[key]]
        values.append(stat(sample))
    values.sort()
    return (values[int(0.025 * iterations)], values[int(0.975 * iterations)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census-summary", type=Path, required=True)
    parser.add_argument("--stage-b", type=Path, help="coded Stage B JSONL")
    parser.add_argument("--stage-c", type=Path, help="coded Stage C JSONL")
    args = parser.parse_args()
    summary = json.loads(args.census_summary.read_text())
    counts = summary["status_counts"]
    hash_bound = sum(counts.get(s, 0) for s in
                     ("file_missing", "range_invalid", "hash_mismatch",
                      "hash_match", "hash_unrecoverable"))
    visible = sum(counts.get(s, 0) for s in
                  ("file_missing", "range_invalid", "hash_mismatch"))
    report = {
        "gate_visibility_D1": visible / hash_bound if hash_bound else None,
        "no_ref_rate": counts.get("no_ref", 0) / summary["total_links"],
        "ref_without_hash_rate": counts.get("ref_without_hash", 0) / summary["total_links"],
        "machine_triage": summary.get("machine_triage_counts", {}),
        "by_tertile": summary["by_tertile"],
    }
    if args.stage_b and args.stage_b.is_file():
        coded = [json.loads(l) for l in args.stage_b.read_text().splitlines() if l.strip()]
        blind = [c for c in coded if c["code"] in ("B2", "B3", "B4", "B5", "B6")]
        report["stage_b"] = {"n": len(coded), "blind_spot": len(blind),
                             "blind_spot_rate": len(blind) / len(coded),
                             "wilson_95": wilson_ci(len(blind), len(coded)),
                             "extend": len(blind) < STAGE_B_MIN_EVENTS and len(coded) == STAGE_B_N}
    if args.stage_c and args.stage_c.is_file():
        coded = [json.loads(l) for l in args.stage_c.read_text().splitlines() if l.strip()]
        denom = [c for c in coded if c["code"] in ("C1", "C2", "C3", "C4")]
        c3 = sum(1 for c in denom if c["code"] == "C3")
        report["stage_c"] = {"n": len(coded), "substantive_rate": c3 / len(denom) if denom else None}
    if "stage_b" in report and "stage_c" in report and report["stage_c"]["substantive_rate"] is not None:
        value = addressable(counts.get("hash_match", 0), counts.get("hash_mismatch", 0),
                            report["stage_b"]["blind_spot_rate"], report["stage_c"]["substantive_rate"])
        report["addressable_D1"] = value
        report["decision"] = ("hash_gate_alone_insufficient"
                              if value < ADDRESSABLE_BASELINE_D1 else "baseline_met")
    print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
