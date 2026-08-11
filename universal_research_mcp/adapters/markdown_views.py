"""Pure Markdown projections for plans and human-readable work logs."""

from __future__ import annotations

from typing import Any


def render_plan_view(record: dict[str, Any]) -> str:
    payload = record.get("payload") or {}
    rows = [f"# {payload.get('title') or record.get('record_id', 'Research plan')}", "", "## Objective", str(payload.get("objective", "Not recorded")), "", "## In scope"]
    rows.extend(f"- {item}" for item in payload.get("in_scope", []))
    rows.extend(["", "## Excluded"])
    rows.extend(f"- {item}" for item in payload.get("out_of_scope", []))
    rows.extend(["", "## Approval references"])
    rows.extend(f"- {item}" for item in record.get("approval_refs", []))
    return "\n".join(rows).rstrip() + "\n"


def render_work_log_view(record: dict[str, Any]) -> str:
    payload = record.get("payload") or {}
    actor = record.get("created_by") or {}
    rows = [f"## {record.get('occurred_at', 'Unknown time')} — {record.get('record_kind', 'record')}", "", f"- Record: {record.get('record_id', 'unknown')}", f"- Status: {record.get('status', 'unknown')}", f"- Recorded by: {actor.get('actor_id', 'unknown')} ({actor.get('actor_type', 'unknown')})"]
    for label, key in (("Expected", "expected"), ("Observed", "observed"), ("Interpretation", "interpretation"), ("Uncertainty", "uncertainty")):
        if key in payload:
            rows.extend(["", f"### {label}", str(payload[key])])
    return "\n".join(rows).rstrip() + "\n"
