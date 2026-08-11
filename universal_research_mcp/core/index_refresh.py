"""Eligibility and health contracts for derived research-search refreshes.

The functions deliberately do not create an index.  An approved execution
adapter may use their output only after a canonical event has been recorded.
"""

from __future__ import annotations

from typing import Any


REFRESH_RECORD_KINDS = frozenset({
    "decision", "execution_session", "observation", "claim", "amendment",
    "audit_finding", "negative_result", "stopped_work", "artifact",
})


def refresh_eligibility(event: dict[str, Any], canonical_recorded: bool) -> dict[str, Any]:
    """Determine whether a canonical event may trigger a derived refresh."""

    if not canonical_recorded:
        return {"eligible": False, "reason": "canonical event has not been recorded"}
    if not isinstance(event, dict):
        return {"eligible": False, "reason": "event must be an object"}
    record_id = event.get("record_id") or event.get("event_id")
    if not isinstance(record_id, str) or not record_id:
        return {"eligible": False, "reason": "event has no stable identifier"}
    kind = event.get("record_kind") or event.get("event_type")
    if kind not in REFRESH_RECORD_KINDS:
        return {"eligible": False, "reason": "event does not change research-state retrieval"}
    return {
        "eligible": True,
        "reason": "canonical research event changes searchable research state",
        "record_id": record_id,
        "refresh_scope": "derived_indexes_only",
    }


def validate_index_health_record(record: dict[str, Any]) -> list[str]:
    """Validate the required machine-readable health result of a refresh attempt."""

    if not isinstance(record, dict):
        return ["index-health record must be an object"]
    required = {
        "schema_version", "status", "index_revision", "event_count", "passage_count",
        "embedding_model", "embedding_dimension", "artifact_hashes", "source_event_ids",
        "retrieval_verification", "failures",
    }
    issues = [f"missing required field: {field}" for field in sorted(required - set(record))]
    if record.get("schema_version") != "research-index-health-v1":
        issues.append("schema_version must be research-index-health-v1")
    if record.get("status") not in {"succeeded", "partial", "failed", "stale"}:
        issues.append("status must be succeeded, partial, failed, or stale")
    for field in ("index_revision", "embedding_model"):
        if not isinstance(record.get(field), str) or not record.get(field, "").strip():
            issues.append(f"{field} must be a non-empty string")
    for field in ("event_count", "passage_count", "embedding_dimension"):
        if not isinstance(record.get(field), int) or record.get(field, -1) < 0:
            issues.append(f"{field} must be a non-negative integer")
    for field in ("artifact_hashes", "source_event_ids", "failures"):
        if not isinstance(record.get(field), list):
            issues.append(f"{field} must be an array")
    verification = record.get("retrieval_verification")
    if not isinstance(verification, dict) or verification.get("status") not in {"passed", "failed", "not_run"}:
        issues.append("retrieval_verification must declare passed, failed, or not_run")
    if record.get("status") == "succeeded" and isinstance(verification, dict) and verification.get("status") != "passed":
        issues.append("a succeeded refresh requires passed retrieval verification")
    return issues
