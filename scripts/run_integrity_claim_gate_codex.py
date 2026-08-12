#!/usr/bin/env python3
"""Execute one isolated Codex development pilot for Integrity & Claim-Gating v1.

This runner records execution telemetry and raw event hashes only.  It never
generates quality labels from model output: each completed run remains pending
until a separate blinded evaluator attaches its rubric result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.contracts import read_jsonl
from benchmarks.integrity_claim_gate import CONDITIONS, validate_bundle, validate_run
from benchmarks.integrity_fixtures import build_development_fixtures


BENCHMARK_ID = "integrity_claim_gate_v1"
RUNNER_SCHEMA = "integrity-claim-gate-execution-manifest/1.0"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _telemetry(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = [event.get("item") for event in events if isinstance(event.get("item"), Mapping)]
    mcp_items = [item for item in items if item.get("type") == "mcp_tool_call"]
    command_items = [item for item in items if item.get("type") == "command_execution"]
    usage: Mapping[str, Any] = {}
    for event in events:
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), Mapping):
            usage = event["usage"]
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = None
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens
    return {
        "usage": {
            "scope": "agent_execution",
            "authority": "provider_reported",
            "input_tokens": input_tokens if isinstance(input_tokens, int) else None,
            "output_tokens": output_tokens if isinstance(output_tokens, int) else None,
            "provider_total_tokens": total_tokens,
            "cached_input_tokens": usage.get("cached_input_tokens") if isinstance(usage.get("cached_input_tokens"), int) else None,
            "cache_write_input_tokens": usage.get("cache_write_input_tokens") if isinstance(usage.get("cache_write_input_tokens"), int) else None,
            "reasoning_tokens": usage.get("reasoning_output_tokens") if isinstance(usage.get("reasoning_output_tokens"), int) else None,
            "billable_tokens": None,
            "standardized_non_overlapping_tokens": total_tokens,
            "total_inclusion_semantics": "input_plus_output" if total_tokens is not None else "unavailable",
            "provider_usage_raw_sha256": "",
        },
        "calls": {
            "mcp_calls_started": len(mcp_items),
            "filesystem_calls_started": len(command_items),
            "claim_gate_calls_started": sum(1 for item in mcp_items if item.get("tool") == "memory_gate_claim"),
        },
        "mcp_tools": sorted({str(item.get("tool")) for item in mcp_items if isinstance(item.get("tool"), str)}),
        "turn_completed": bool(usage),
        "usage_limit_reached": any(
            event.get("type") == "error" and "usage limit" in str(event.get("message", "")).lower()
            for event in events
        ),
    }


def _checksum_manifest(fixture: Mapping[str, Any], path: Path) -> None:
    entries = []
    for relative in fixture["registered_paths"]:
        source = path.parent / relative
        entries.append({"path": relative, "sha256": _sha256_file(source)})
    _write_json(path, {"schema_version": "integrity-checksum-manifest/1.0", "entries": entries})


def _prompt(task: Mapping[str, Any], condition: str) -> str:
    common = (
        "This is a synthetic development benchmark. Do not edit files, run tests, use the network, "
        "or infer facts not supported by supplied evidence. Give a concise decision with exact evidence citations.\n\n"
        f"Task: {task['prompt']}\n"
        f"Claim type: {task['claim_type']}; materiality: {task['materiality']}."
    )
    if condition == "filesystem":
        return common + "\nUse ordinary local file discovery and reading only."
    if condition == "filesystem_manifest":
        return common + (
            "\nUse ordinary local file discovery and reading only. Verify every cited file against "
            "checksum-manifest.json before relying on it."
        )
    if condition == "mcp_evidence_only":
        return common + (
            "\nUse only the urtrial MCP for evidence: search candidates and fetch exact evidence. "
            "Do not use shell or direct filesystem tools. Do not call the claim gate."
        )
    if condition == "mcp_claim_gate":
        return common + (
            "\nUse only the urtrial MCP for evidence: search candidates and fetch exact evidence. "
            "Do not use shell or direct filesystem tools. For every material conclusion, pass each returned "
            "claim_gate_reference unchanged to memory_gate_claim before stating the conclusion. If the gate "
            "blocks or evidence is unavailable, abstain and report the blocker."
        )
    raise ValueError(f"unsupported condition: {condition}")


def _command(*, codex: str, model: str, reasoning_effort: str, fixture_root: Path, repo_root: Path,
             condition: str, output: Path, prompt: str) -> list[str]:
    command = [
        codex, "exec", "--ephemeral", "--json", "--sandbox", "read-only", "--model", model,
        "-c", f'model_reasoning_effort="{reasoning_effort}"', "-C", str(fixture_root), "-o", str(output),
    ]
    if condition.startswith("mcp"):
        command.extend([
            "-c", f'mcp_servers.urtrial.command="{sys.executable}"',
            "-c", 'mcp_servers.urtrial.args=["-m","universal_research_mcp.cli","serve","--root","' + str(fixture_root) + '","--no-auto-index"]',
            "-c", 'mcp_servers.urtrial.env={PYTHONPATH="' + str(repo_root) + '"}',
        ])
    command.append(prompt)
    return command


def _configuration_fingerprint(task: Mapping[str, Any], condition: str, model: str, reasoning_effort: str,
                               prompt: str, fixture: Mapping[str, Any]) -> str:
    canonical = json.dumps({
        "task_id": task["task_id"], "condition": condition, "model": model, "reasoning_effort": reasoning_effort,
        "prompt": prompt, "fixture": fixture["post_setup_source_sha256"],
        "index_fingerprint": fixture["index_fingerprint"],
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    # The run contract reserves identifiers for lower-case names.  Prefixing
    # the digest preserves the full hash while making its representation
    # unambiguous and schema-valid regardless of its first hexadecimal digit.
    return "sha256." + _sha256_bytes(canonical)


def _run_one(*, task: Mapping[str, Any], fixture: Mapping[str, Any], condition: str, model: str, reasoning_effort: str, codex: str,
             repo_root: Path, output_root: Path, timeout_seconds: int) -> dict[str, Any]:
    fixture_root = Path(str(fixture["root"]))
    if condition == "filesystem_manifest":
        _checksum_manifest(fixture, fixture_root / "checksum-manifest.json")
    trial_id = f"{task['task_id']}.{condition}.r1"
    trial_dir = output_root / "trials" / trial_id
    trial_dir.mkdir(parents=True, exist_ok=False)
    events_path = trial_dir / "events.jsonl"
    final_path = trial_dir / "final.txt"
    prompt = _prompt(task, condition)
    command = _command(codex=codex, model=model, reasoning_effort=reasoning_effort, fixture_root=fixture_root, repo_root=repo_root,
                       condition=condition, output=final_path, prompt=prompt)
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        events_path.write_text(completed.stdout, encoding="utf-8")
        (trial_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
        run_status = "completed" if completed.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        events_path.write_text(stdout, encoding="utf-8")
        (trial_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
        run_status = "stopped"
    elapsed_ms = round((time.monotonic() - started) * 1000)
    raw_events = events_path.read_bytes()
    events = _parse_events(events_path)
    summary = _telemetry(events)
    summary["usage"]["provider_usage_raw_sha256"] = _sha256_bytes(raw_events)
    if summary["usage_limit_reached"]:
        run_status = "stopped"
    elif not summary["turn_completed"] and run_status == "completed":
        run_status = "failed"
    record: dict[str, Any] = {
        "schema_version": "integrity-claim-gate-run/1.0",
        "benchmark_id": BENCHMARK_ID,
        "trial_id": trial_id,
        "task_id": task["task_id"],
        "condition": condition,
        "repetition": 1,
        "run_status": run_status,
        "configuration_fingerprint": _configuration_fingerprint(task, condition, model, reasoning_effort, prompt, fixture),
        "model": {
            "provider": "openai_codex", "requested_model": model,
            "requested_reasoning_effort": reasoning_effort,
        },
        "usage": summary["usage"],
        "latency_ms": elapsed_ms,
        "calls": summary["calls"],
        "evaluation_status": "pending",
        "evaluation": None,
        "artifacts": {
            "events_jsonl_sha256": _sha256_file(events_path),
            "final_answer_sha256": _sha256_file(final_path) if final_path.exists() else None,
            "stderr_sha256": _sha256_file(trial_dir / "stderr.txt"),
        },
        "execution_notes": {
            "mcp_tools_observed": summary["mcp_tools"],
            "raw_outputs_retained_outside_repository": True,
            "source_fixture_development_only": True,
            "terminal_execution_blocker": "provider_usage_limit" if summary["usage_limit_reached"] else None,
        },
    }
    validate_run(record)
    _write_json(trial_dir / "run.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New, empty directory outside the repository")
    parser.add_argument("--model", required=True, help="Explicit Codex model identifier recorded in every run")
    parser.add_argument(
        "--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="low",
        help="Explicit Codex reasoning setting; it is held fixed across paired conditions",
    )
    parser.add_argument("--approval-ref", required=True, help="Explicit human approval reference for this development execution")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--condition", action="append", choices=sorted(CONDITIONS))
    parser.add_argument("--max-runs", type=int)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"refusing non-empty output directory: {args.output}")
    if args.timeout_seconds < 1 or args.max_runs is not None and args.max_runs < 1:
        raise SystemExit("timeout and max-runs must be positive")
    repo_root = Path(__file__).resolve().parents[1]
    tasks = read_jsonl(args.tasks)
    validate_bundle(tasks, [])
    args.output.mkdir(parents=True, exist_ok=True)
    fixtures_root = args.output / "fixtures"
    fixtures = build_development_fixtures(tasks, fixtures_root)
    fixture_by_task = {str(item["task_id"]): item for item in fixtures}
    selected_conditions = args.condition or sorted(CONDITIONS)
    planned = [(task, condition) for task in tasks for condition in selected_conditions]
    if args.max_runs is not None:
        planned = planned[:args.max_runs]
    _write_json(args.output / "execution-manifest.json", {
        "schema_version": RUNNER_SCHEMA, "benchmark_id": BENCHMARK_ID,
        "created_at": datetime.now(UTC).isoformat(), "approval_ref": args.approval_ref,
        "model": args.model, "reasoning_effort": args.reasoning_effort,
        "planned_run_count": len(planned), "conditions": selected_conditions,
        "development_only": True, "evaluation_policy": "pending_blinded_evaluation_required",
    })
    runs: list[dict[str, Any]] = []
    for task, condition in planned:
        runs.append(_run_one(task=task, fixture=fixture_by_task[str(task["task_id"])], condition=condition,
                             model=args.model, reasoning_effort=args.reasoning_effort, codex=args.codex, repo_root=repo_root,
                             output_root=args.output, timeout_seconds=args.timeout_seconds))
        with (args.output / "runs.pending.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(runs[-1], ensure_ascii=False, sort_keys=True) + "\n")
        if runs[-1]["execution_notes"]["terminal_execution_blocker"] is not None:
            break
    terminal_blockers = [
        row["execution_notes"]["terminal_execution_blocker"]
        for row in runs if row["execution_notes"]["terminal_execution_blocker"] is not None
    ]
    _write_json(args.output / "execution-summary.json", {
        "schema_version": "integrity-claim-gate-execution-summary/1.0",
        "planned_run_count": len(planned), "attempted_run_count": len(runs),
        "completed_run_count": sum(row["run_status"] == "completed" for row in runs),
        "terminal_blocker": terminal_blockers[0] if terminal_blockers else None,
        "evaluation_status": "pending",
    })
    print(json.dumps({"run_count": len(runs), "output": str(args.output), "evaluation_status": "pending",
                      "terminal_blocker": terminal_blockers[0] if terminal_blockers else None}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
