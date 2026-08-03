"""Incremental watcher helpers layered over source-range correction v1."""

from __future__ import annotations

from typing import Any

from scripts.research_event_corrections import (
    SUPPORTED_SOURCE_POINTERS,
    source_range_correction_count,
)


def source_range_correction_target_ids(
    events: list[dict[str, Any]],
) -> set[str]:
    """Return strictly validated targets named by source-range amendments."""

    targets: set[str] = set()
    for amendment in events:
        observed = amendment.get("observed")
        if not isinstance(observed, dict):
            continue
        pointer = observed.get("corrected_json_pointer")
        if pointer is None:
            continue
        if amendment.get("event_type") != "amendment" or amendment.get("status") != "completed":
            raise ValueError(
                f"{amendment.get('event_id')}: source correction must be a completed amendment"
            )
        if pointer not in SUPPORTED_SOURCE_POINTERS:
            raise ValueError(
                f"{amendment['event_id']}: unsupported corrected JSON pointer {pointer!r}"
            )
        corrects = [
            relation.get("target")
            for relation in amendment.get("relations", [])
            if isinstance(relation, dict) and relation.get("type") == "corrects"
        ]
        if len(corrects) != 1 or not isinstance(corrects[0], str):
            raise ValueError(
                f"{amendment['event_id']}: source correction needs exactly one corrects target"
            )
        target_id = corrects[0]
        if observed.get("corrected_event_id") != target_id:
            raise ValueError(
                f"{amendment['event_id']}: corrected_event_id disagrees with corrects relation"
            )
        targets.add(target_id)
    return targets


__all__ = ["source_range_correction_count", "source_range_correction_target_ids"]
