#!/usr/bin/env python3
"""Bind externally produced blinded verdicts to execution records.

Reuses the repository evaluator's parsing, binding, and reporting functions
unchanged; only the judge differs (a condition-blinded Claude session instead
of a codex subprocess). Each batch directory must contain result.json in the
exact schema the repository evaluator defines.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path("/home/mpwsl2/paper/universal_research_mcp/.claude/worktrees/mcp-codex-rules-review-a443ff")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import evaluate_integrity_claim_gate_blinded as ev  # noqa: E402
from benchmarks.contracts import read_jsonl  # noqa: E402
from benchmarks.integrity_claim_gate import integrity_claim_gate_report, validate_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--evaluation-root", type=Path, required=True,
                        help="Directory previously created with --prepare-only, now holding result.json per batch")
    parser.add_argument("--judge-label", default="claude_session_condition_blinded")
    args = parser.parse_args()

    tasks = read_jsonl(args.tasks)
    runs = read_jsonl(args.execution_root / "runs.pending.jsonl")
    validate_bundle(tasks, runs)
    bindings = json.loads((args.evaluation_root / "private-binding.json").read_text(encoding="utf-8"))
    batch_manifest = json.loads((args.evaluation_root / "batch-manifest.json").read_text(encoding="utf-8"))

    results: dict[str, dict] = {}
    for batch in batch_manifest["batches"]:
        result_path = Path(batch["path"]) / "result.json"
        parsed = ev._parse_result(result_path, set(batch["evaluation_ids"]))
        overlap = set(results).intersection(parsed)
        if overlap:
            raise SystemExit(f"duplicate verdicts across batches: {sorted(overlap)[:3]}")
        results.update(parsed)
    if len(results) != len(runs):
        raise SystemExit(f"verdicts cover {len(results)} of {len(runs)} runs")

    evaluated = ev._attach_evaluation(args.execution_root, tasks, runs, bindings, results)
    out = args.evaluation_root / "runs.evaluated.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for record in evaluated:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    report = integrity_claim_gate_report(tasks, evaluated)
    summary = {
        "schema_version": ev.EVALUATOR_SCHEMA,
        "evaluation_status": "completed",
        "judge": args.judge_label,
        "run_count": len(evaluated),
        "report": report,
    }
    (args.evaluation_root / "evaluation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps({"evaluated": len(evaluated), "output": str(out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
