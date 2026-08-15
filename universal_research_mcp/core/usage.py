"""Read and summarize exact host/provider token observations.

The supported package reports only persisted counts.  It never estimates usage
from text length, elapsed time, model names, or reserved budgets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from universal_research_mcp.runtime import ProjectPaths


USAGE_SCHEMA_VERSION = "usage-observation/1.0"
SUMMARY_SCHEMA_VERSION = "usage-summary/1.0"
USAGE_SOURCES = frozenset({"provider_reported", "host_reported", "unavailable"})
ACTIVITY_CATEGORIES = frozenset({
    "agent_generation", "code_generation", "command_execution",
    "skill_invocation", "visualization", "retrieval", "review", "other",
})


class UsageObservationError(ValueError):
    """Raised when a persisted observation is not factual or well formed."""


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsageObservationError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    return None if value is None else _nonempty(value, label)


def _count(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise UsageObservationError(f"{label} must be a non-negative integer")
    return value


def validate_usage_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != USAGE_SCHEMA_VERSION:
        raise UsageObservationError("unsupported usage observation schema")
    source = value.get("source")
    category = value.get("activity_category")
    if source not in USAGE_SOURCES:
        raise UsageObservationError("source is not supported")
    if category not in ACTIVITY_CATEGORIES:
        raise UsageObservationError("activity_category is not supported")
    token_usage = value.get("token_usage")
    if not isinstance(token_usage, Mapping):
        raise UsageObservationError("token_usage must be an object")
    unavailable_reason = value.get("unavailable_reason")
    if source == "unavailable":
        if any(token_usage.get(key) is not None for key in ("input_tokens", "output_tokens", "total_tokens")):
            raise UsageObservationError("unavailable usage must not contain token counts")
        unavailable_reason = _nonempty(unavailable_reason, "unavailable_reason")
        counts = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    else:
        input_tokens = _count(token_usage.get("input_tokens"), "input_tokens")
        output_tokens = _count(token_usage.get("output_tokens"), "output_tokens")
        total_tokens = _count(token_usage.get("total_tokens"), "total_tokens")
        if input_tokens + output_tokens != total_tokens:
            raise UsageObservationError("input_tokens plus output_tokens must equal total_tokens")
        if unavailable_reason is not None:
            raise UsageObservationError("observed usage must not contain unavailable_reason")
        counts = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
    return {
        "schema_version": USAGE_SCHEMA_VERSION,
        "observed_at": _nonempty(value.get("observed_at"), "observed_at"),
        "run_id": _nonempty(value.get("run_id"), "run_id"),
        "workflow_id": _nonempty(value.get("workflow_id"), "workflow_id"),
        "agent_id": _nonempty(value.get("agent_id"), "agent_id"),
        "activity_category": category,
        "source": source,
        "token_usage": counts,
        "provider_id": _optional_string(value.get("provider_id"), "provider_id"),
        "model": _optional_string(value.get("model"), "model"),
        "operation_ref": _optional_string(value.get("operation_ref"), "operation_ref"),
        "unavailable_reason": unavailable_reason,
    }


def read_usage_observations(root: str | Path) -> list[dict[str, Any]]:
    path = ProjectPaths.from_root(root).resolve_relative("data/governance/usage.jsonl")
    if not path.exists():
        return []
    observations: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            decoded = json.loads(raw)
            if not isinstance(decoded, Mapping):
                raise UsageObservationError("usage observation must be an object")
            observations.append(validate_usage_observation(decoded))
        except (json.JSONDecodeError, UsageObservationError) as exc:
            raise UsageObservationError(
                f"invalid usage observation at {path}:{line_number}: {exc}"
            ) from exc
    return observations


def summarize_usage(
    observations: Iterable[Mapping[str, Any]], *, run_id: str | None = None,
) -> dict[str, Any]:
    normalized = [validate_usage_observation(item) for item in observations]
    if run_id is not None:
        _nonempty(run_id, "run_id")
        normalized = [item for item in normalized if item["run_id"] == run_id]
    observed = [item for item in normalized if item["source"] != "unavailable"]
    total = sum(int(item["token_usage"]["total_tokens"]) for item in observed)
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
        "total_observed_tokens": total,
        "category_totals": [
            {
                "activity_category": category,
                "total_tokens": amount,
                "share_of_observed_total_percent": (
                    None if total == 0 else round(amount * 100 / total, 6)
                ),
            }
            for category, amount in sorted(categories.items())
        ],
        "source_totals": [
            {"source": source, "total_tokens": amount}
            for source, amount in sorted(sources.items())
        ],
        "unavailable": [
            {"activity_category": key[0], "reason": key[1], "count": count}
            for key, count in sorted(unavailable.items())
        ],
    }


__all__ = [
    "UsageObservationError", "read_usage_observations", "summarize_usage",
    "validate_usage_observation",
]
