"""Pure, dependency-free checks for Universal Research canonical JSONL.

The module never writes a ledger.  Its output is suitable for a write adapter,
an index builder, or a read-only auditor to decide whether a record is safe to
accept.  Legacy event records remain supported as a compatibility input.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


CORE_RECORD_KINDS = {
    "research_plan", "protocol", "approval", "execution_session",
    "observation", "decision", "claim", "artifact", "amendment",
    "audit_finding", "contribution", "negative_result", "stopped_work",
}
CORE_STATUSES = {
    "draft", "proposed", "approved", "active", "completed", "stopped",
    "rejected", "superseded",
}
RELATION_TYPES = {
    "governed_by", "authorized_by", "uses_protocol", "generated_from",
    "derived_from", "evaluated_on", "supported_by", "refuted_by",
    "validated_by", "decided_from", "corrects", "supersedes",
    "requires_review", "contributed_to",
}


@dataclass(frozen=True)
class ValidationIssue:
    record_id: str
    path: str
    message: str
    severity: str = "blocking"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read one JSON object per nonblank line without modifying the file."""

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        loaded = json.loads(line)
        if not isinstance(loaded, dict):
            raise ValueError(f"{path}:{line_number}: record must be a JSON object")
        records.append(loaded)
    return records


def _issue(record: dict[str, Any], path: str, message: str) -> ValidationIssue:
    return ValidationIssue(str(record.get("record_id") or record.get("event_id") or "<unknown>"), path, message)


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_core_record(record: dict[str, Any], known_ids: set[str] | None = None) -> list[ValidationIssue]:
    """Validate safety-relevant core invariants without external libraries."""

    issues: list[ValidationIssue] = []
    for field in ("schema_version", "record_id", "record_kind", "occurred_at", "recorded_at", "status"):
        if not _is_nonempty_string(record.get(field)):
            issues.append(_issue(record, f"/{field}", "required non-empty string"))
    if record.get("schema_version") != "core/1.0":
        issues.append(_issue(record, "/schema_version", "must be core/1.0"))
    if record.get("record_kind") not in CORE_RECORD_KINDS:
        issues.append(_issue(record, "/record_kind", "unknown core record kind"))
    if record.get("status") not in CORE_STATUSES:
        issues.append(_issue(record, "/status", "unknown core status"))

    actor = record.get("created_by")
    if not isinstance(actor, dict) or not _is_nonempty_string(actor.get("actor_id")):
        issues.append(_issue(record, "/created_by", "actor_id is required"))
    elif actor.get("actor_type") not in {"human", "ai", "external_system"}:
        issues.append(_issue(record, "/created_by/actor_type", "must identify human, ai, or external_system"))

    if not isinstance(record.get("payload"), dict):
        issues.append(_issue(record, "/payload", "must be an object"))

    for index, relation in enumerate(record.get("relations", [])):
        if not isinstance(relation, dict):
            issues.append(_issue(record, f"/relations/{index}", "must be an object"))
            continue
        if relation.get("type") not in RELATION_TYPES:
            issues.append(_issue(record, f"/relations/{index}/type", "unknown relation type"))
        target_id = relation.get("target_id")
        if not _is_nonempty_string(target_id):
            issues.append(_issue(record, f"/relations/{index}/target_id", "required non-empty target ID"))
        elif known_ids is not None and target_id not in known_ids:
            issues.append(_issue(record, f"/relations/{index}/target_id", "target does not exist in this ledger"))

    if record.get("record_kind") == "execution_session" and record.get("status") == "active":
        if not record.get("approval_refs"):
            issues.append(_issue(record, "/approval_refs", "active session requires explicit approval"))

    if record.get("record_kind") == "claim" and record.get("payload", {}).get("support_status") == "supported":
        evidence = record.get("source_refs", [])
        if not any(isinstance(item, dict) and item.get("verification_status") == "human_verified" for item in evidence):
            issues.append(_issue(record, "/source_refs", "supported claim requires human-verified evidence"))

    if record.get("record_kind") == "amendment":
        payload = record.get("payload", {})
        corrects = [item for item in record.get("relations", []) if isinstance(item, dict) and item.get("type") == "corrects"]
        if len(corrects) != 1:
            issues.append(_issue(record, "/relations", "amendment requires exactly one corrects relation"))
        for field in ("path", "recorded_value", "corrected_value", "reason"):
            if field not in payload:
                issues.append(_issue(record, f"/payload/{field}", "amendment must record before/after values and reason"))

    return issues


def validate_legacy_event(event: dict[str, Any]) -> list[ValidationIssue]:
    """Validate the existing event-ledger shape without rewriting it."""

    issues: list[ValidationIssue] = []
    for field in ("event_id", "date", "event_type", "status", "project", "summary"):
        if not _is_nonempty_string(event.get(field)):
            issues.append(_issue(event, f"/{field}", "required non-empty string"))
    source = event.get("source")
    if source is not None:
        if not isinstance(source, dict):
            issues.append(_issue(event, "/source", "must be an object when present"))
        else:
            start, end = source.get("line_start"), source.get("line_end")
            if (start is None) != (end is None):
                issues.append(_issue(event, "/source", "line_start and line_end must appear together"))
            if start is not None and (not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start):
                issues.append(_issue(event, "/source", "invalid source line range"))
    return issues


def validate_records(records: Iterable[dict[str, Any]]) -> list[ValidationIssue]:
    materialized = list(records)
    core_ids = {str(record["record_id"]) for record in materialized if _is_nonempty_string(record.get("record_id"))}
    seen: set[str] = set()
    issues: list[ValidationIssue] = []
    for record in materialized:
        identifier = str(record.get("record_id") or record.get("event_id") or "")
        if identifier in seen:
            issues.append(_issue(record, "/record_id", "duplicate record identifier"))
        seen.add(identifier)
        if record.get("schema_version") == "core/1.0":
            issues.extend(validate_core_record(record, core_ids))
        else:
            issues.extend(validate_legacy_event(record))
    return issues
