"""Deterministic paired summaries for validated MCP A/B benchmark records."""

from __future__ import annotations

import random
from statistics import fmean, median
from typing import Any, Callable

from benchmarks.contracts import CONDITIONS, METRIC_FIELDS, validate_bundle


DEFAULT_WEIGHTS = {field: 1 / len(METRIC_FIELDS) for field in METRIC_FIELDS}


def quality_score(run: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    if run.get("evaluation_status") != "completed":
        raise ValueError("scoring requires a completed blinded evaluation")
    selected = weights or DEFAULT_WEIGHTS
    if set(selected) != set(METRIC_FIELDS) or abs(sum(selected.values()) - 1.0) > 1e-9:
        raise ValueError("quality weights must cover all metrics and sum to 1")
    return sum(float(run["evaluation"][field]) * selected[field] for field in METRIC_FIELDS)


def comparison_tokens(run: dict[str, Any]) -> float:
    usage = run["usage"]
    value = usage.get("provider_total_tokens")
    if value is None:
        value = usage.get("standardized_non_overlapping_tokens")
    if value is None:
        raise ValueError("run has no authoritative or standardized comparison token count")
    return float(value)


def _bootstrap_mean_ci(values: list[float], *, seed: int, samples: int = 5000) -> list[float]:
    if not values:
        raise ValueError("cannot bootstrap an empty paired sample")
    if len(values) == 1:
        return [values[0], values[0]]
    generator = random.Random(seed)
    estimates = sorted(
        fmean(generator.choice(values) for _ in values)
        for _ in range(samples)
    )
    return [estimates[int(0.025 * (samples - 1))], estimates[int(0.975 * (samples - 1))]]


def _condition_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    qualities = [quality_score(row) for row in rows]
    totals = [comparison_tokens(row) for row in rows]
    successful = [row for row in rows if row["evaluation"]["task_success"] == 1]
    return {
        "run_count": len(rows),
        "audit_ready_success_rate": fmean(float(row["evaluation"]["audit_ready_success"]) for row in rows),
        "mean_quality": fmean(qualities),
        "median_total_tokens": median(totals),
        "mean_total_tokens": fmean(totals),
        "total_model_calls": sum(row["calls"]["model_calls_started"] for row in rows),
        "total_mcp_calls": sum(row["calls"]["mcp_calls_started"] for row in rows),
        "total_filesystem_calls": sum(row["calls"]["filesystem_calls_started"] for row in rows),
        "total_tool_response_bytes": sum(row["payload_bytes"]["tool_response_bytes"] for row in rows),
        "mean_latency_ms": fmean(float(row["latency_ms"]) for row in rows),
        "total_normalized_list_cost_usd": sum(float(row["cost"]["normalized_list_amount"]) for row in rows),
        "unsupported_claim_count": sum(row["evaluation"]["unsupported_claim_count"] for row in rows),
        "policy_violation_count": sum(row["evaluation"]["policy_violation_count"] for row in rows),
        "tokens_per_successful_run": (
            sum(comparison_tokens(row) for row in rows) / len(successful)
            if successful else None
        ),
    }


def paired_report(tasks: list[dict[str, Any]], runs: list[dict[str, Any]], *, bootstrap_seed: int = 20260804) -> dict[str, Any]:
    validate_bundle(tasks, runs, require_pairs=True)
    completed = [row for row in runs if row["run_status"] == "completed" and row["evaluation_status"] == "completed"]
    by_pair: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in completed:
        by_pair.setdefault((row["task_id"], row["repetition"]), {})[row["condition"]] = row
    complete_pairs = {key: value for key, value in by_pair.items() if set(value) == CONDITIONS}
    if len(complete_pairs) != len(by_pair):
        raise ValueError("scoring refuses incomplete evaluated pairs")

    def deltas(accessor: Callable[[dict[str, Any]], float]) -> list[float]:
        return [accessor(pair["mcp"]) - accessor(pair["filesystem"]) for pair in complete_pairs.values()]

    quality_deltas = deltas(quality_score)
    audit_ready_deltas = deltas(lambda row: float(row["evaluation"]["audit_ready_success"]))
    token_deltas = deltas(comparison_tokens)
    latency_deltas = deltas(lambda row: float(row["latency_ms"]))
    cost_deltas = deltas(lambda row: float(row["cost"]["normalized_list_amount"]))
    conditions = {
        condition: _condition_summary([row for row in completed if row["condition"] == condition])
        for condition in sorted(CONDITIONS)
    }
    return {
        "schema_version": "benchmark-report/1.0",
        "pair_count": len(complete_pairs),
        "conditions": conditions,
        "paired_deltas_mcp_minus_filesystem": {
            "audit_ready_success_rate": fmean(audit_ready_deltas),
            "audit_ready_success_95pct_bootstrap_ci": _bootstrap_mean_ci(audit_ready_deltas, seed=bootstrap_seed + 2),
            "mean_quality": fmean(quality_deltas),
            "mean_quality_95pct_bootstrap_ci": _bootstrap_mean_ci(quality_deltas, seed=bootstrap_seed),
            "mean_total_tokens": fmean(token_deltas),
            "mean_total_tokens_95pct_bootstrap_ci": _bootstrap_mean_ci(token_deltas, seed=bootstrap_seed + 1),
            "mean_latency_ms": fmean(latency_deltas),
            "mean_cost_usd": fmean(cost_deltas),
        },
        "interpretation_boundary": "Candidate retrieval and token efficiency are measured; causal or superiority claims require the preregistered confirmatory run and human review.",
    }
