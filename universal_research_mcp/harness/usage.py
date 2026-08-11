"""Append-only, source-labelled token-usage observations.

This module intentionally distinguishes a token count reported by a provider or
host from telemetry that the current host does not expose.  It never derives a
token count from prompt bytes, model names, elapsed time, or a budget
reservation.  Those values are useful for preflight limits, but are not actual
usage.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from universal_research_mcp.runtime import ProjectPaths

from .ledger import AppendOnlyJsonlSink


USAGE_SCHEMA_VERSION = "usage-observation/1.0"
SUMMARY_SCHEMA_VERSION = "usage-summary/1.0"
USAGE_SOURCES = frozenset({"provider_reported", "host_reported", "unavailable"})
ACTIVITY_CATEGORIES = frozenset({
    "agent_generation",
    "code_generation",
    "command_execution",
    "skill_invocation",
    "visualization",
    "retrieval",
    "review",
    "other",
})
UsageSource = Literal["provider_reported", "host_reported", "unavailable"]


class UsageObservationError(ValueError):
    """Raised when a usage record cannot truthfully be represented."""


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsageObservationError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _nonempty(value, label)


def _nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise UsageObservationError(f"{label} must be a non-negative integer")
    return value


def usage_observation(
    *,
    run_id: str,
    workflow_id: str,
    agent_id: str,
    activity_category: str,
    source: UsageSource,
    total_tokens: int | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider_id: str | None = None,
    model: str | None = None,
    operation_ref: str | None = None,
    unavailable_reason: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build one validated usage record without storing prompt or command text."""

    if activity_category not in ACTIVITY_CATEGORIES:
        raise UsageObservationError("activity_category is not supported")
    if source not in USAGE_SOURCES:
        raise UsageObservationError("source is not supported")
    if source == "unavailable":
        if total_tokens is not None or input_tokens is not None or output_tokens is not None:
            raise UsageObservationError("unavailable usage must not contain token counts")
        unavailable_reason = _nonempty(unavailable_reason, "unavailable_reason")
    else:
        total_tokens = _nonnegative_int(total_tokens, "total_tokens")
        input_tokens = _nonnegative_int(input_tokens, "input_tokens")
        output_tokens = _nonnegative_int(output_tokens, "output_tokens")
        if input_tokens + output_tokens != total_tokens:
            raise UsageObservationError("input_tokens plus output_tokens must equal total_tokens")
        if unavailable_reason is not None:
            raise UsageObservationError("observed usage must not contain unavailable_reason")
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": USAGE_SCHEMA_VERSION,
        "observed_at": _nonempty(observed_at, "observed_at"),
        "run_id": _nonempty(run_id, "run_id"),
        "workflow_id": _nonempty(workflow_id, "workflow_id"),
        "agent_id": _nonempty(agent_id, "agent_id"),
        "activity_category": activity_category,
        "source": source,
        "token_usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        "provider_id": _optional_string(provider_id, "provider_id"),
        "model": _optional_string(model, "model"),
        "operation_ref": _optional_string(operation_ref, "operation_ref"),
        "unavailable_reason": unavailable_reason,
    }


def provider_generation_usage_observation(
    *,
    run_id: str,
    workflow_id: str,
    agent_id: str,
    provider_id: str,
    model: str,
    operation_ref: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> dict[str, Any]:
    """Represent one generation response without treating missing usage as zero."""

    counts = (input_tokens, output_tokens, total_tokens)
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
        raise UsageObservationError("provider usage values must be non-negative integers")
    if input_tokens + output_tokens != total_tokens:
        raise UsageObservationError("provider usage totals are inconsistent")
    if total_tokens == 0:
        return usage_observation(
            run_id=run_id,
            workflow_id=workflow_id,
            agent_id=agent_id,
            activity_category="agent_generation",
            source="unavailable",
            total_tokens=None,
            provider_id=provider_id,
            model=model,
            operation_ref=operation_ref,
            unavailable_reason="provider_response_did_not_report_token_usage",
        )
    return usage_observation(
        run_id=run_id,
        workflow_id=workflow_id,
        agent_id=agent_id,
        activity_category="agent_generation",
        source="provider_reported",
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider_id=provider_id,
        model=model,
        operation_ref=operation_ref,
    )


class UsageRecorder:
    """Write observations to a dedicated append-only derived-runtime record."""

    def __init__(self, root: str | Path) -> None:
        self._sink = AppendOnlyJsonlSink(root, "data/governance/usage.jsonl")

    def __call__(self, observation: Mapping[str, Any]) -> bool:
        validated = validate_usage_observation(observation)
        return self._sink(validated)


def validate_usage_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one JSONL record read from the usage stream."""

    if not isinstance(value, Mapping):
        raise UsageObservationError("usage observation must be an object")
    if value.get("schema_version") != USAGE_SCHEMA_VERSION:
        raise UsageObservationError("unsupported usage observation schema")
    token_usage = value.get("token_usage")
    if not isinstance(token_usage, Mapping):
        raise UsageObservationError("token_usage must be an object")
    source = value.get("source")
    return usage_observation(
        run_id=value.get("run_id"),
        workflow_id=value.get("workflow_id"),
        agent_id=value.get("agent_id"),
        activity_category=value.get("activity_category"),
        source=source,
        total_tokens=token_usage.get("total_tokens"),
        input_tokens=token_usage.get("input_tokens"),
        output_tokens=token_usage.get("output_tokens"),
        provider_id=value.get("provider_id"),
        model=value.get("model"),
        operation_ref=value.get("operation_ref"),
        unavailable_reason=value.get("unavailable_reason"),
        observed_at=value.get("observed_at"),
    )


def read_usage_observations(root: str | Path) -> list[dict[str, Any]]:
    """Read the local usage stream without creating or modifying it."""

    path = ProjectPaths.from_root(root).resolve_relative("data/governance/usage.jsonl")
    if not path.exists():
        return []
    observations: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            decoded = json.loads(raw)
            observations.append(validate_usage_observation(decoded))
        except (json.JSONDecodeError, UsageObservationError) as exc:
            raise UsageObservationError(
                f"invalid usage observation at {path}:{line_number}: {exc}"
            ) from exc
    return observations


def summarize_usage(
    observations: Iterable[Mapping[str, Any]], *, run_id: str | None = None,
) -> dict[str, Any]:
    """Return factual totals; unavailable records never enter the denominator."""

    normalized = [validate_usage_observation(item) for item in observations]
    if run_id is not None:
        _nonempty(run_id, "run_id")
        normalized = [item for item in normalized if item["run_id"] == run_id]
    observed = [item for item in normalized if item["source"] != "unavailable"]
    total_observed_tokens = sum(int(item["token_usage"]["total_tokens"]) for item in observed)
    categories: dict[str, int] = {}
    sources: dict[str, int] = {}
    unavailable: dict[tuple[str, str], int] = {}
    for item in observed:
        category = str(item["activity_category"])
        categories[category] = categories.get(category, 0) + int(item["token_usage"]["total_tokens"])
        source = str(item["source"])
        sources[source] = sources.get(source, 0) + int(item["token_usage"]["total_tokens"])
    for item in normalized:
        if item["source"] == "unavailable":
            key = (str(item["activity_category"]), str(item["unavailable_reason"]))
            unavailable[key] = unavailable.get(key, 0) + 1
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": run_id,
        "observation_count": len(normalized),
        "observed_observation_count": len(observed),
        "unavailable_observation_count": len(normalized) - len(observed),
        "total_observed_tokens": total_observed_tokens,
        "category_totals": [
            {
                "activity_category": category,
                "total_tokens": total,
                "share_of_observed_total_percent": (
                    None if total_observed_tokens == 0 else round(total * 100 / total_observed_tokens, 6)
                ),
            }
            for category, total in sorted(categories.items())
        ],
        "source_totals": [
            {
                "source": source,
                "total_tokens": total,
                "share_of_observed_total_percent": (
                    None if total_observed_tokens == 0 else round(total * 100 / total_observed_tokens, 6)
                ),
            }
            for source, total in sorted(sources.items())
        ],
        "unavailable_observations": [
            {
                "activity_category": category,
                "reason": reason,
                "observation_count": count,
            }
            for (category, reason), count in sorted(unavailable.items())
        ],
        "denominator": "total_observed_tokens_only",
    }
