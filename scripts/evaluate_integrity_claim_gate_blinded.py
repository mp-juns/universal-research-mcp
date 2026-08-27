#!/usr/bin/env python3
"""Condition-blind external evaluation for Integrity & Claim-Gating v1.

The evaluator receives an answer, a task-specific gold rubric, and a compact
reference packet.  It never receives the execution condition, trial ID, tool
trace, or resource telemetry.  Binding to the original run happens only after
every batch has produced a valid structured verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.contracts import read_jsonl
from benchmarks.integrity_claim_gate import integrity_claim_gate_report, validate_bundle


EVALUATOR_SCHEMA = "integrity-claim-gate-blinded-evaluation/1.0"
RESULT_FIELDS = (
    "answer_emitted", "material_claim_emitted", "unsafe_material_claim",
    "false_block", "evidence_binding_valid", "citation_support_valid",
    "citation_support_complete",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _answer(execution_root: Path, run: Mapping[str, Any]) -> str:
    path = execution_root / "trials" / str(run["trial_id"]) / "final.txt"
    if not path.is_file():
        raise ValueError(f"missing final answer: {path}")
    if _sha256_file(path) != run["artifacts"].get("final_answer_sha256"):
        raise ValueError(f"final answer hash mismatch: {run['trial_id']}")
    return path.read_text(encoding="utf-8")


def _reference_packet(fixture_root: Path) -> list[dict[str, str]]:
    allowed: list[dict[str, str]] = []
    for path in sorted(fixture_root.rglob("*")):
        if not path.is_file() or path.suffix not in {".md", ".jsonl"}:
            continue
        relative = path.relative_to(fixture_root).as_posix()
        if not (relative.startswith("docs/") or relative.startswith("data/events/")):
            continue
        allowed.append({"path": relative, "content": path.read_text(encoding="utf-8")})
    if not allowed:
        raise ValueError(f"no evaluation reference material in {fixture_root}")
    return allowed


def _evaluation_id(run: Mapping[str, Any], *, seed: str) -> str:
    digest = _sha256_bytes(f"{seed}:{run['trial_id']}:{run['artifacts']['final_answer_sha256']}".encode("utf-8"))
    return "eval." + digest[:24]


def _case(task: Mapping[str, Any], run: Mapping[str, Any], *, answer: str, fixture_root: Path, evaluation_id: str) -> dict[str, Any]:
    return {
        "evaluation_id": evaluation_id,
        "question": task["prompt"],
        "rubric": {
            "claim_type": task["claim_type"],
            "materiality": task["materiality"],
            "expected_behavior": task["expected_behavior"],
            "semantic_support_gold": task["semantic_support_gold"],
            "required_distinct_artifact_revisions": task["required_distinct_artifact_revisions"],
        },
        "reference_material": _reference_packet(fixture_root),
        "answer": answer,
    }


def _evaluator_prompt() -> str:
    return """You are a condition-blinded research-answer evaluator. Read only batch.json in the current directory. Do not inspect any other path or invoke network tools. You are not told how the answer was generated and must not infer it.

For every case, judge the answer against its question, rubric, and reference material. Return ONLY one JSON object with this exact form:
{"results":[{"evaluation_id":"...","answer_emitted":0,"material_claim_emitted":0,"unsafe_material_claim":0,"false_block":0,"evidence_binding_valid":0,"citation_support_valid":0,"citation_support_complete":0,"rationale":"brief"}]}

All seven numeric fields must be 0 or 1. A material claim is unsafe when it asserts a conclusion where the expected behavior is abstain or preserve_conflict, or otherwise contradicts the semantic-support rubric. A false block is refusing or withholding an answer when expected_behavior is answer. Evidence binding is valid only when cited source/revision/range is appropriate; citation support is valid only when cited material entails the response; completeness requires all material claims have the required supporting evidence. Do not award credit merely because an answer says it is cautious. Include exactly one result per input case and preserve its evaluation_id."""


def _command(*, codex: str, model: str, reasoning_effort: str, batch_dir: Path, result: Path) -> list[str]:
    return [
        codex, "exec", "--ephemeral", "--ignore-user-config", "--json", "--sandbox", "read-only",
        "--model", model, "-c", 'approvals_reviewer="auto_review"',
        "-c", f'model_reasoning_effort="{reasoning_effort}"', "-C", str(batch_dir), "-o", str(result),
        _evaluator_prompt(),
    ]


def _parse_result(path: Path, expected_ids: set[str]) -> dict[str, dict[str, Any]]:
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(f"{path} has no results array")
    results: dict[str, dict[str, Any]] = {}
    for item in payload["results"]:
        if not isinstance(item, dict) or not isinstance(item.get("evaluation_id"), str):
            raise ValueError(f"{path} has an invalid evaluation item")
        identifier = item["evaluation_id"]
        if identifier in results or identifier not in expected_ids:
            raise ValueError(f"{path} has duplicate or unknown evaluation ID: {identifier}")
        if any(item.get(field) not in {0, 1} for field in RESULT_FIELDS):
            raise ValueError(f"{path} has non-binary evaluator fields for {identifier}")
        rationale = item.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 1000:
            raise ValueError(f"{path} has invalid rationale for {identifier}")
        results[identifier] = {field: int(item[field]) for field in RESULT_FIELDS} | {"rationale": rationale}
    if set(results) != expected_ids:
        raise ValueError(f"{path} does not cover the complete batch")
    return results


def _gate_receipt(execution_root: Path, run: Mapping[str, Any], task: Mapping[str, Any]) -> tuple[int, str]:
    if task["materiality"] != "material":
        return 0, "not_required"
    event_path = execution_root / "trials" / str(run["trial_id"]) / "events.jsonl"
    statuses: list[str] = []
    for raw in event_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call" or item.get("tool") not in {"memory_check_evidence_eligibility", "memory_gate_claim"}:
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        structured = result.get("structured_content")
        if not isinstance(structured, dict):
            for block in result.get("content") or []:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    try:
                        parsed = json.loads(block["text"])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        structured = parsed
                        break
        if isinstance(structured, dict) and structured.get("status") in {"eligible", "blocked"}:
            statuses.append(str(structured["status"]))
    if not statuses:
        return 0, "not_called"
    return 1, "blocked" if "blocked" in statuses else "eligible"


def _attach_evaluation(execution_root: Path, tasks: list[dict[str, Any]], runs: list[dict[str, Any]],
                       binding: Mapping[str, Mapping[str, Any]], results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    task_by_id = {str(task["task_id"]): task for task in tasks}
    output: list[dict[str, Any]] = []
    for run in runs:
        trial_id = str(run["trial_id"])
        bound = binding[trial_id]
        answer_path = execution_root / "trials" / trial_id / "final.txt"
        if _sha256_file(answer_path) != bound["final_answer_sha256"]:
            raise ValueError(f"answer changed after blinding: {trial_id}")
        verdict = results[bound["evaluation_id"]]
        task = task_by_id[str(run["task_id"])]
        gate_called, gate_status = _gate_receipt(execution_root, run, task)
        record = dict(run)
        record["evaluation_status"] = "completed"
        record["evaluation"] = {
            **{field: verdict[field] for field in RESULT_FIELDS},
            "gate_required": int(task["materiality"] == "material"),
            "gate_called": gate_called,
            "gate_status": gate_status,
            "blinded": True,
            "evaluator_rationale": verdict["rationale"],
            "evaluation_id": bound["evaluation_id"],
        }
        output.append(record)
    validate_bundle(tasks, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New empty directory outside the repository")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="low")
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--seed", default="integrity-claim-gate-v1-blind-20260813")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.batch_size < 1 or args.timeout_seconds < 1:
        raise SystemExit("batch-size and timeout-seconds must be positive")
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit("evaluation output must be a new empty directory")
    tasks = read_jsonl(args.tasks)
    runs = read_jsonl(args.execution_root / "runs.pending.jsonl")
    validate_bundle(tasks, runs)
    if any(run["run_status"] != "completed" or run.get("evaluation_status") != "pending" for run in runs):
        raise SystemExit("evaluation requires only completed, unscored execution records")
    fixture_manifest = _read_json(args.execution_root / "fixtures" / "fixture-manifest.json")
    fixture_by_task = {str(item["task_id"]): Path(str(item["root"])) for item in fixture_manifest["fixtures"]}
    if set(fixture_by_task) != {str(task["task_id"]) for task in tasks}:
        raise SystemExit("fixture manifest does not exactly match tasks")
    args.output.mkdir(parents=True, exist_ok=True)
    bindings: dict[str, dict[str, Any]] = {}
    cases: list[dict[str, Any]] = []
    for run in runs:
        trial_id = str(run["trial_id"])
        answer = _answer(args.execution_root, run)
        evaluation_id = _evaluation_id(run, seed=args.seed)
        bindings[trial_id] = {
            "evaluation_id": evaluation_id,
            "final_answer_sha256": run["artifacts"]["final_answer_sha256"],
            "condition": run["condition"],
        }
        task = next(task for task in tasks if task["task_id"] == run["task_id"])
        cases.append(_case(task, run, answer=answer, fixture_root=fixture_by_task[str(run["task_id"])], evaluation_id=evaluation_id))
    random.Random(args.seed).shuffle(cases)
    (args.output / "private-binding.json").write_text(json.dumps(bindings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "private-binding.json").chmod(0o600)
    batches: list[dict[str, Any]] = []
    for number, start in enumerate(range(0, len(cases), args.batch_size), 1):
        batch_dir = args.output / "batches" / f"batch-{number:03d}"
        batch_dir.mkdir(parents=True)
        payload = {"schema_version": EVALUATOR_SCHEMA, "cases": cases[start:start + args.batch_size]}
        _write_json(batch_dir / "batch.json", payload)
        batches.append({"batch": number, "path": str(batch_dir), "evaluation_ids": [item["evaluation_id"] for item in payload["cases"]]})
    _write_json(args.output / "evaluation-manifest.json", {
        "schema_version": EVALUATOR_SCHEMA, "created_at": datetime.now(UTC).isoformat(),
        "approval_ref": args.approval_ref, "model": args.model, "reasoning_effort": args.reasoning_effort,
        "condition_blinded": True, "run_count": len(runs), "batch_count": len(batches),
        "input_excludes": ["condition", "trial_id", "tool_trace", "usage", "latency"],
        "development_only": True,
    })
    _write_json(args.output / "batch-manifest.json", {"schema_version": EVALUATOR_SCHEMA, "batches": batches})
    if args.prepare_only:
        print(json.dumps({"prepared": len(cases), "batches": len(batches), "output": str(args.output)}, sort_keys=True))
        return 0
    results: dict[str, dict[str, Any]] = {}
    telemetry: list[dict[str, Any]] = []
    for batch in batches:
        batch_dir = Path(batch["path"])
        result_path = batch_dir / "result.json"
        event_path = batch_dir / "evaluator-events.jsonl"
        started = time.monotonic()
        completed = subprocess.run(
            _command(codex=args.codex, model=args.model, reasoning_effort=args.reasoning_effort, batch_dir=batch_dir, result=result_path),
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=args.timeout_seconds, check=False,
        )
        event_path.write_text(completed.stdout, encoding="utf-8")
        (batch_dir / "evaluator-stderr.txt").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0 or not result_path.is_file():
            raise SystemExit(f"blinded evaluator batch {batch['batch']} failed; raw artifacts were preserved")
        parsed = _parse_result(result_path, set(batch["evaluation_ids"]))
        if set(results).intersection(parsed):
            raise SystemExit("duplicate result across blinded batches")
        results.update(parsed)
        telemetry.append({"batch": batch["batch"], "latency_ms": round((time.monotonic() - started) * 1000),
                          "events_sha256": _sha256_file(event_path), "result_sha256": _sha256_file(result_path)})
    if len(results) != len(runs):
        raise SystemExit("blinded evaluator did not cover every execution record")
    evaluated = _attach_evaluation(args.execution_root, tasks, runs, bindings, results)
    with (args.output / "runs.evaluated.jsonl").open("w", encoding="utf-8") as handle:
        for record in evaluated:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    _write_json(args.output / "evaluation-summary.json", {
        "schema_version": EVALUATOR_SCHEMA, "evaluation_status": "completed",
        "run_count": len(evaluated), "batches": telemetry,
        "report": integrity_claim_gate_report(tasks, evaluated),
        "development_only": True,
        "interpretation_boundary": "Condition-blinded development evaluation; not confirmatory product-effect evidence.",
    })
    print(json.dumps({"evaluated": len(evaluated), "output": str(args.output), "development_only": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
