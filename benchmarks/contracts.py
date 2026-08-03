"""Dependency-free validation for MCP A/B benchmark task and run records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


TASK_SCHEMA_VERSION = "benchmark-task/1.0"
RUN_SCHEMA_VERSION = "benchmark-run/1.0"
CONDITIONS = {"filesystem", "mcp"}
TASK_CATEGORIES = {
    "evidence_retrieval", "claim_verification", "approval_governance",
    "integrity_change", "negative_result", "contribution_attribution",
    "amendment_history", "uncertainty_calibration",
}
METRIC_FIELDS = (
    "task_success", "factual_correctness", "evidence_grounding",
    "citation_validity", "policy_compliance", "uncertainty_calibration",
)
TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "provider_total_tokens",
    "cached_input_tokens", "cache_write_input_tokens", "reasoning_tokens",
    "billable_tokens", "standardized_non_overlapping_tokens",
)
COUNT_FIELDS = (
    "model_calls_started", "model_calls_completed", "model_calls_failed",
    "model_retries", "mcp_calls_started", "mcp_calls_completed",
    "mcp_calls_failed", "filesystem_calls_started",
    "filesystem_calls_completed", "filesystem_calls_failed",
    "other_tool_calls_started", "other_tool_calls_completed",
    "other_tool_calls_failed",
)
BYTE_FIELDS = ("model_input_bytes", "model_output_bytes", "tool_request_bytes", "tool_response_bytes", "tool_content_bytes")
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]+$")


class BenchmarkValidationError(ValueError):
    """Raised when a benchmark contract fails closed."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BenchmarkValidationError(f"{path}:{number}: malformed JSON") from exc
        if not isinstance(row, dict):
            raise BenchmarkValidationError(f"{path}:{number}: row must be an object")
        rows.append(row)
    return rows


def _require_id(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or ID_PATTERN.fullmatch(value) is None:
        raise BenchmarkValidationError(f"{field} must be a stable lower-case identifier")
    return value


def _nonnegative_int(record: dict[str, Any], field: str, *, context: str) -> int:
    value = record.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BenchmarkValidationError(f"{context}.{field} must be a non-negative integer")
    return value


def _optional_nonnegative_int(record: dict[str, Any], field: str, *, context: str) -> int | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BenchmarkValidationError(f"{context}.{field} must be null or a non-negative integer")
    return value


def validate_task(task: dict[str, Any]) -> None:
    if task.get("schema_version") != TASK_SCHEMA_VERSION:
        raise BenchmarkValidationError("task schema_version must be benchmark-task/1.0")
    _require_id(task, "task_id")
    _require_id(task, "source_bundle_id")
    if task.get("category") not in TASK_CATEGORIES:
        raise BenchmarkValidationError(f"unknown task category: {task.get('category')!r}")
    if not isinstance(task.get("prompt"), str) or not task["prompt"].strip():
        raise BenchmarkValidationError("task prompt is required")
    if task.get("holdout") is not True:
        raise BenchmarkValidationError("benchmark tasks must be marked holdout")
    expected = task.get("expected")
    if not isinstance(expected, dict):
        raise BenchmarkValidationError("task expected contract is required")
    if not isinstance(expected.get("facts"), list) or not expected["facts"]:
        raise BenchmarkValidationError("task expected.facts must be a non-empty array")
    citations = expected.get("citations")
    if not isinstance(citations, list):
        raise BenchmarkValidationError("task expected.citations must be an array")
    for citation in citations:
        if not isinstance(citation, dict) or not isinstance(citation.get("path"), str):
            raise BenchmarkValidationError("expected citation path is required")
        start, end = citation.get("start_line"), citation.get("end_line")
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise BenchmarkValidationError("expected citation line range is invalid")


def validate_run(run: dict[str, Any]) -> None:
    if run.get("schema_version") != RUN_SCHEMA_VERSION:
        raise BenchmarkValidationError("run schema_version must be benchmark-run/1.0")
    for field in ("benchmark_id", "trial_id", "task_id", "configuration_fingerprint"):
        _require_id(run, field)
    if run.get("condition") not in CONDITIONS:
        raise BenchmarkValidationError("condition must be filesystem or mcp")
    if not isinstance(run.get("repetition"), int) or run["repetition"] < 1:
        raise BenchmarkValidationError("repetition must be a positive integer")
    if run.get("run_status") not in {"completed", "failed", "stopped"}:
        raise BenchmarkValidationError("run_status must preserve completed, failed, or stopped")
    isolation = run.get("isolation")
    required_isolation = {
        "fresh_workspace", "read_only_source", "independent_derived_index",
        "no_cross_condition_state", "secret_via_environment_only",
    }
    if not isinstance(isolation, dict) or any(isolation.get(key) is not True for key in required_isolation):
        raise BenchmarkValidationError("run isolation assertions are incomplete")
    usage = run.get("usage")
    if not isinstance(usage, dict) or usage.get("authority") not in {"provider_reported", "gateway_reported", "estimated", "mixed"}:
        raise BenchmarkValidationError("usage authority must identify provider-reported or estimated tokens")
    if usage.get("scope") != "agent_execution":
        raise BenchmarkValidationError("run usage scope must be agent_execution")
    for field in TOKEN_FIELDS:
        _optional_nonnegative_int(usage, field, context="usage")
    if usage.get("total_inclusion_semantics") not in {"input_plus_output", "provider_native", "unavailable"}:
        raise BenchmarkValidationError("usage total inclusion semantics are required")
    if usage["total_inclusion_semantics"] == "input_plus_output":
        if None in (usage["provider_total_tokens"], usage["input_tokens"], usage["output_tokens"]):
            raise BenchmarkValidationError("input_plus_output semantics require input, output, and provider total")
        if usage["provider_total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            raise BenchmarkValidationError("provider total must equal input + output under declared semantics")
    if usage["input_tokens"] is not None and usage["cached_input_tokens"] is not None and usage["cached_input_tokens"] > usage["input_tokens"]:
        raise BenchmarkValidationError("cached input tokens cannot exceed input tokens")
    if usage["output_tokens"] is not None and usage["reasoning_tokens"] is not None and usage["reasoning_tokens"] > usage["output_tokens"]:
        raise BenchmarkValidationError("reasoning tokens cannot exceed output tokens")
    if not isinstance(usage.get("provider_usage_raw_sha256"), str):
        raise BenchmarkValidationError("raw provider usage artifact hash is required")
    calls = run.get("calls")
    if not isinstance(calls, dict):
        raise BenchmarkValidationError("calls object is required")
    for field in COUNT_FIELDS:
        _nonnegative_int(calls, field, context="calls")
    if run["condition"] == "filesystem" and calls["mcp_calls_started"] != 0:
        raise BenchmarkValidationError("filesystem baseline cannot make MCP calls")
    payload = run.get("payload_bytes")
    if not isinstance(payload, dict):
        raise BenchmarkValidationError("payload_bytes object is required")
    for field in BYTE_FIELDS:
        _nonnegative_int(payload, field, context="payload_bytes")
    _optional_nonnegative_int(payload, "estimated_context_tokens", context="payload_bytes")
    if payload.get("estimated_context_tokens") is not None and not isinstance(payload.get("tokenizer_id"), str):
        raise BenchmarkValidationError("estimated context tokens require tokenizer_id")
    latency = run.get("latency_ms")
    if not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0:
        raise BenchmarkValidationError("latency_ms must be non-negative")
    evaluation_status = run.get("evaluation_status")
    if evaluation_status not in {"pending", "completed"}:
        raise BenchmarkValidationError("evaluation_status must be pending or completed")
    if evaluation_status == "completed":
        evaluation = run.get("evaluation")
        if not isinstance(evaluation, dict) or evaluation.get("blinded") is not True:
            raise BenchmarkValidationError("completed evaluation must be blinded")
        if evaluation.get("audit_ready_success") not in {0, 1}:
            raise BenchmarkValidationError("evaluation.audit_ready_success must be binary")
        for field in METRIC_FIELDS:
            value = evaluation.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                raise BenchmarkValidationError(f"evaluation.{field} must be in [0, 1]")
        for field in ("unsupported_claim_count", "policy_violation_count"):
            _nonnegative_int(evaluation, field, context="evaluation")
    cost = run.get("cost")
    if not isinstance(cost, dict) or cost.get("currency") != "USD":
        raise BenchmarkValidationError("cost must use a versioned USD pricing snapshot")
    if not isinstance(cost.get("normalized_list_amount"), (int, float)) or cost["normalized_list_amount"] < 0:
        raise BenchmarkValidationError("cost.normalized_list_amount must be non-negative")
    provider_cost = cost.get("provider_billed_amount")
    if provider_cost is not None and (not isinstance(provider_cost, (int, float)) or provider_cost < 0):
        raise BenchmarkValidationError("provider billed cost must be null or non-negative")
    _require_id(cost, "pricing_snapshot_id")


def validate_bundle(tasks: Iterable[dict[str, Any]], runs: Iterable[dict[str, Any]], *, require_pairs: bool) -> None:
    task_rows, run_rows = list(tasks), list(runs)
    task_ids: set[str] = set()
    for task in task_rows:
        validate_task(task)
        if task["task_id"] in task_ids:
            raise BenchmarkValidationError(f"duplicate task_id: {task['task_id']}")
        task_ids.add(task["task_id"])
    trial_keys: set[tuple[str, str, int]] = set()
    pairs: dict[tuple[str, int], set[str]] = {}
    for run in run_rows:
        validate_run(run)
        if run["task_id"] not in task_ids:
            raise BenchmarkValidationError(f"run references unknown task: {run['task_id']}")
        key = (run["task_id"], run["condition"], run["repetition"])
        if key in trial_keys:
            raise BenchmarkValidationError(f"duplicate condition run: {key}")
        trial_keys.add(key)
        pairs.setdefault((run["task_id"], run["repetition"]), set()).add(run["condition"])
    if require_pairs:
        incomplete = sorted(key for key, conditions in pairs.items() if conditions != CONDITIONS)
        if incomplete:
            raise BenchmarkValidationError(f"unpaired A/B runs: {incomplete}")
