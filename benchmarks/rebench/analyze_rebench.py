#!/usr/bin/env python3
"""Preregistered analysis for rebench-ablation-v1 (commit precedes runs).

Statistical grounds (quotes verified and RAG-bound; see
benchmarks/protocols/rebench-ablation-v1.citations.json):
- Wilson score interval for single proportions
  (cit_newcombe1998_score_single: tail-area and score methods
  "are recommended for use").
- Newcombe hybrid score interval for risk differences
  (cit_newcombe1998_hybrid_difference: combining Wilson score intervals
  "performs well, and is readily implemented irrespective of sample size").
- Mid-p McNemar as the paired test; the exact conditional test is
  abandoned (cit_fagerland2013_midp: "We do not recommend use of the
  McNemar exact conditional test in any situation.").
- Rule-of-three bound reported for zero cells
  (cit_eypasch1995_rule_of_three: upper 95% limit = 3/n for n > 30).
- Activation endpoint design per cit_metatool2023_activation; cheap-
  baseline arm and cost co-reporting per cit_agentsmatter2024_cost_baseline;
  model-rater validation per cit_mtbench2023_judge_agreement.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

ARMS = ("filesystem", "mcp_natural", "manifest_prompt")
NEGATIVE_CONTROL_STATES = {"poisoned_before_registration"}
GATE_TOOL = "memory_check_evidence_eligibility"


def wilson_ci(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def newcombe_rd_ci(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    l1, u1 = wilson_ci(k1, n1)
    l2, u2 = wilson_ci(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    return (p1 - p2 - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2),
            p1 - p2 + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def midp_mcnemar(b: int, c: int) -> float:
    """Two-sided mid-p McNemar on discordant pairs (b, c)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    def pmf(x: int) -> float:
        return math.comb(n, x) * 0.5 ** n
    tail = sum(pmf(x) for x in range(0, k))
    p = 2 * (tail + 0.5 * pmf(k))
    return min(1.0, p)


def rule_of_three(n: int) -> float:
    return 3.0 / n if n else float("nan")


def cluster_rd_ci(rows, arm_a: str, arm_b: str, iterations: int = 10000,
                  seed: int = 20260829) -> tuple[float, float]:
    rng = random.Random(seed)
    by_task = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_task[r["task_id"]][r["condition"]].append(r["det_unsafe_assertion"])
    keys = sorted(by_task)
    vals = []
    for _ in range(iterations):
        ka = na = kb = nb = 0
        for key in (rng.choice(keys) for _ in keys):
            cell = by_task[key]
            ka += sum(cell[arm_a]); na += len(cell[arm_a])
            kb += sum(cell[arm_b]); nb += len(cell[arm_b])
        vals.append(ka / max(na, 1) - kb / max(nb, 1))
    vals.sort()
    return (vals[int(0.025 * iterations)], vals[int(0.975 * iterations)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--det-scored", type=Path, required=True,
                        help="JSONL rows: task_id, condition, evidence_state, expected_behavior, "
                             "det_unsafe_assertion, det_expected_outcome, activated, tokens, latency_ms")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(l) for l in args.det_scored.read_text().splitlines() if l.strip()]
    fault = [r for r in rows if r["expected_behavior"] in ("abstain", "preserve_conflict")]
    primary = [r for r in fault if r["evidence_state"] not in NEGATIVE_CONTROL_STATES]
    clean = [r for r in rows if r["expected_behavior"] not in ("abstain", "preserve_conflict")]
    report: dict = {"arms": {}, "pairwise": {}, "negative_control": {}, "activation": {}}
    for arm in ARMS:
        pf = [r for r in primary if r["condition"] == arm]
        cl = [r for r in clean if r["condition"] == arm]
        k = sum(r["det_unsafe_assertion"] for r in pf)
        report["arms"][arm] = {
            "primary_fault_unsafe": f"{k}/{len(pf)}",
            "wilson_95": [round(x, 4) for x in wilson_ci(k, len(pf))],
            "rule_of_three_bound_if_zero": round(rule_of_three(len(pf)), 4) if k == 0 else None,
            "clean_expected": f"{sum(r['det_expected_outcome'] for r in cl)}/{len(cl)}",
            "mean_tokens": round(sum(r.get("tokens") or 0 for r in rows if r["condition"] == arm)
                                 / max(1, len([r for r in rows if r["condition"] == arm]))),
        }
        nc = [r for r in fault if r["condition"] == arm and r["evidence_state"] in NEGATIVE_CONTROL_STATES]
        report["negative_control"][arm] = f"{sum(r['det_unsafe_assertion'] for r in nc)}/{len(nc)}"
    for a, b in (("filesystem", "mcp_natural"), ("filesystem", "manifest_prompt"),
                 ("manifest_prompt", "mcp_natural")):
        ra = [r for r in primary if r["condition"] == a]
        rb = [r for r in primary if r["condition"] == b]
        ka, kb = sum(r["det_unsafe_assertion"] for r in ra), sum(r["det_unsafe_assertion"] for r in rb)
        pair_a = {(r["task_id"], r.get("rep")): r["det_unsafe_assertion"] for r in ra}
        pair_b = {(r["task_id"], r.get("rep")): r["det_unsafe_assertion"] for r in rb}
        disc_b = sum(1 for key in pair_a if pair_a[key] and not pair_b.get(key, False))
        disc_c = sum(1 for key in pair_a if not pair_a[key] and pair_b.get(key, False))
        report["pairwise"][f"{a}-minus-{b}"] = {
            "rd": round(ka / max(len(ra), 1) - kb / max(len(rb), 1), 4),
            "newcombe_95": [round(x, 4) for x in newcombe_rd_ci(ka, len(ra), kb, len(rb))],
            "task_cluster_bootstrap_95": [round(x, 4) for x in cluster_rd_ci(primary, a, b)],
            "midp_mcnemar_p": midp_mcnemar(disc_b, disc_c),
        }
    nat_fault = [r for r in primary if r["condition"] == "mcp_natural"]
    act = [r for r in nat_fault if r.get("activated")]
    noact = [r for r in nat_fault if not r.get("activated")]
    report["activation"] = {
        "rate": f"{len(act)}/{len(nat_fault)}",
        "wilson_95": [round(x, 4) for x in wilson_ci(len(act), max(len(nat_fault), 1))],
        "unsafe_given_activated": f"{sum(r['det_unsafe_assertion'] for r in act)}/{len(act)}",
        "unsafe_given_not_activated": f"{sum(r['det_unsafe_assertion'] for r in noact)}/{len(noact)}",
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
