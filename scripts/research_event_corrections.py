"""Resolve narrow append-only corrections for derived Research Memory views."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SUPPORTED_SOURCE_POINTERS = {
    "/source/line_start": "line_start",
    "/source/line_end": "line_end",
}


def source_range_correction_count(events: list[dict[str, Any]]) -> int:
    """Count canonical events that declare the supported correction contract."""

    return sum(
        event.get("event_type") == "amendment"
        and isinstance(event.get("observed"), dict)
        and event["observed"].get("corrected_json_pointer")
        in SUPPORTED_SOURCE_POINTERS
        and any(
            isinstance(relation, dict) and relation.get("type") == "corrects"
            for relation in event.get("relations", [])
        )
        for event in events
    )


def apply_source_range_corrections(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return corrected derived events while preserving canonical input objects.

    Only completed amendment events with a single ``corrects`` relation and a
    supported source-range JSON pointer are applied. The amendment must name
    the value currently present in the target, so conflicting or stale
    corrections fail closed.
    """

    resolved = deepcopy(events)
    by_id = {event["event_id"]: event for event in resolved}
    if len(by_id) != len(resolved):
        raise ValueError("Duplicate event_id while resolving source corrections")

    applied: list[dict[str, Any]] = []
    for amendment in events:
        observed = amendment.get("observed")
        if not isinstance(observed, dict):
            continue
        pointer = observed.get("corrected_json_pointer")
        corrects = [
            relation.get("target")
            for relation in amendment.get("relations", [])
            if isinstance(relation, dict) and relation.get("type") == "corrects"
        ]
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
        if len(corrects) != 1 or not isinstance(corrects[0], str):
            raise ValueError(
                f"{amendment['event_id']}: source correction needs exactly one corrects target"
            )

        target_id = corrects[0]
        if observed.get("corrected_event_id") != target_id:
            raise ValueError(
                f"{amendment['event_id']}: corrected_event_id disagrees with corrects relation"
            )
        target = by_id.get(target_id)
        if target is None:
            raise ValueError(f"{amendment['event_id']}: correction target is missing: {target_id}")

        field = SUPPORTED_SOURCE_POINTERS[pointer]
        recorded_key = f"recorded_{field}"
        corrected_key = f"corrected_{field}"
        recorded = observed.get(recorded_key)
        corrected = observed.get(corrected_key)
        if not isinstance(recorded, int) or not isinstance(corrected, int) or corrected < 1:
            raise ValueError(
                f"{amendment['event_id']}: correction values must be positive integers"
            )

        source = dict(target.get("source") or {})
        if source.get(field) != recorded:
            raise ValueError(
                f"{amendment['event_id']}: recorded {field}={recorded} does not match "
                f"current target value {source.get(field)!r}"
            )
        source[field] = corrected
        line_start = source.get("line_start")
        line_end = source.get("line_end")
        if (
            line_start is not None
            and line_end is not None
            and (not isinstance(line_start, int) or not isinstance(line_end, int) or line_start > line_end)
        ):
            raise ValueError(f"{amendment['event_id']}: corrected source range is invalid")

        corrected_target = dict(target)
        corrected_target["source"] = source
        by_id[target_id] = corrected_target
        resolved[next(index for index, event in enumerate(resolved) if event["event_id"] == target_id)] = corrected_target
        applied.append(
            {
                "amendment_event_id": amendment["event_id"],
                "target_event_id": target_id,
                "json_pointer": pointer,
                "recorded_value": recorded,
                "corrected_value": corrected,
            }
        )

    return resolved, applied
