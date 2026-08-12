from __future__ import annotations

import json
from pathlib import Path

from benchmarks.contracts import read_jsonl
from benchmarks.integrity_claim_gate import (
    IntegrityBenchmarkValidationError,
    integrity_claim_gate_report,
    validate_bundle,
)
from scripts.run_integrity_claim_gate_codex import _command, _configuration_fingerprint, _run_key
from scripts.run_integrity_claim_gate_codex import _telemetry
from scripts.evaluate_integrity_claim_gate_blinded import _parse_result


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "benchmarks/fixtures/integrity-claim-gate-v1/tasks.development.jsonl"


def _run(task: dict[str, object], condition: str, *, unsafe: int = 0, gate_called: int | None = None) -> dict[str, object]:
    material = task["materiality"] == "material"
    needs_gate = int(material)
    called = (int(condition == "mcp_claim_gate" and material) if gate_called is None else gate_called)
    fault = task["evidence_state"] != "current_supported"
    return {
        "schema_version": "integrity-claim-gate-run/1.0",
        "benchmark_id": "integrity_claim_gate_v1",
        "trial_id": f"{task['task_id']}.{condition}.r1",
        "task_id": task["task_id"],
        "condition": condition,
        "repetition": 1,
        "run_status": "completed",
        "configuration_fingerprint": "fingerprint_v1",
        "usage": {"provider_total_tokens": 100 if condition == "filesystem" else 160},
        "latency_ms": 1000 if condition == "filesystem" else 1400,
        "calls": {
            "mcp_calls_started": int(condition.startswith("mcp")),
            "filesystem_calls_started": 1,
            "claim_gate_calls_started": called,
        },
        "evaluation": {
            "answer_emitted": int(not fault),
            "material_claim_emitted": int(not fault and material),
            "gate_required": needs_gate,
            "gate_called": called,
            "gate_status": "eligible" if called and not fault else "blocked" if called else "not_called",
            "unsafe_material_claim": unsafe,
            "false_block": 0,
            "evidence_binding_valid": int(not fault),
            "citation_support_valid": int(not fault),
            "citation_support_complete": int(not fault),
        },
    }


def test_development_matrix_has_24_tasks_and_every_evidence_state() -> None:
    tasks = read_jsonl(TASKS)
    validate_bundle(tasks, [])
    assert len(tasks) == 24
    assert {task["evidence_state"] for task in tasks} == {
        "current_supported", "post_index_mutation", "path_reuse", "line_range_drift",
        "stale_derived_index", "unregistered_source", "missing_evidence",
        "conflicting_evidence", "withdrawn_evidence", "superseded_amendment",
        "valid_but_irrelevant", "poisoned_before_registration",
    }


def test_report_separates_safety_coverage_and_gate_bypass() -> None:
    tasks = read_jsonl(TASKS)
    runs = []
    for task in tasks:
        for condition in ("filesystem", "filesystem_manifest", "mcp_evidence_only", "mcp_claim_gate"):
            unsafe = int(condition == "filesystem" and task["evidence_state"] == "post_index_mutation")
            runs.append(_run(task, condition, unsafe=unsafe))
    report = integrity_claim_gate_report(tasks, runs)
    assert report["conditions"]["filesystem"]["integrity_fault_unsafe_assertion_rate"] > 0
    assert report["conditions"]["mcp_claim_gate"]["integrity_fault_unsafe_assertion_rate"] == 0
    assert report["conditions"]["mcp_claim_gate"]["clean_supported_claim_coverage"] == 1
    assert report["conditions"]["mcp_claim_gate"]["routine_over_gating_rate"] == 0

    mutation = next(task for task in tasks if task["task_id"] == "igc.mutation.release")
    bypass = _run(mutation, "mcp_claim_gate", gate_called=0)
    bypass["evaluation"]["answer_emitted"] = 1
    bypass["evaluation"]["material_claim_emitted"] = 1
    report = integrity_claim_gate_report([mutation], [_run(mutation, "filesystem", unsafe=0), bypass])
    assert report["conditions"]["mcp_claim_gate"]["integrity_fault_unsafe_assertion_rate"] == 1


def test_rejects_clean_task_with_fault_injection() -> None:
    task = read_jsonl(TASKS)[0]
    task["fault_injected_at"] = "after_index_before_fetch"
    try:
        validate_bundle([task], [])
    except IntegrityBenchmarkValidationError as exc:
        assert "current evidence" in str(exc)
    else:
        raise AssertionError("clean task with fault injection was accepted")


def test_pending_execution_telemetry_is_valid_but_not_scored() -> None:
    task = read_jsonl(TASKS)[0]
    run = _run(task, "mcp_claim_gate")
    run["evaluation_status"] = "pending"
    run["evaluation"] = None
    validate_bundle([task], [run])
    report = integrity_claim_gate_report([task], [run])
    condition = report["conditions"]["mcp_claim_gate"]
    assert condition["run_count"] == 0
    assert condition["unscored_completed_run_count"] == 1


def test_execution_configuration_fingerprint_is_a_schema_safe_digest() -> None:
    task = read_jsonl(TASKS)[0]
    fingerprint = _configuration_fingerprint(
        task, "filesystem", "gpt-5.6-terra", "low", "prompt",
        {"post_setup_source_sha256": {}, "index_fingerprint": "digest"},
    )
    assert fingerprint.startswith("sha256.")
    assert len(fingerprint) == len("sha256.") + 64


def test_usage_limit_is_marked_as_a_terminal_execution_blocker_signal() -> None:
    telemetry = _telemetry([{"type": "error", "message": "You've hit your usage limit."}])
    assert telemetry["usage_limit_reached"] is True


def test_runner_excludes_user_config_to_keep_mcp_arms_isolated(tmp_path: Path) -> None:
    command = _command(
        codex="codex", model="gpt-5.6-terra", reasoning_effort="low", fixture_root=tmp_path,
        repo_root=ROOT, condition="mcp_claim_gate", output=tmp_path / "answer.txt", prompt="task",
    )
    assert "--ignore-user-config" in command
    assert "--approve-for-me" not in command
    assert 'approvals_reviewer="auto_review"' in command


def test_run_key_prevents_duplicate_resume_trials() -> None:
    task = read_jsonl(TASKS)[0]
    run = _run(task, "filesystem")
    assert _run_key(run) == ("igc.clean.release", "filesystem", 1)


def test_blinded_evaluator_result_requires_exact_binary_coverage(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"results": [{
        "evaluation_id": "eval.a", "answer_emitted": 1, "material_claim_emitted": 1,
        "unsafe_material_claim": 0, "false_block": 0, "evidence_binding_valid": 1,
        "citation_support_valid": 1, "citation_support_complete": 1, "rationale": "supported",
    }]}), encoding="utf-8")
    assert _parse_result(result_path, {"eval.a"}) == {
        "eval.a": {
            "answer_emitted": 1, "material_claim_emitted": 1, "unsafe_material_claim": 0,
            "false_block": 0, "evidence_binding_valid": 1, "citation_support_valid": 1,
            "citation_support_complete": 1, "rationale": "supported",
        }
    }
