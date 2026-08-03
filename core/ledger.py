"""Pure, dependency-free checks for Universal Research canonical JSONL.

The module never writes a ledger.  Its output is suitable for a write adapter,
an index builder, or a read-only auditor to decide whether a record is safe to
accept.  Legacy event records remain supported as a compatibility input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
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
CORE_TOP_LEVEL_FIELDS = {
    "schema_version", "record_id", "record_kind", "study_id", "occurred_at",
    "recorded_at", "status", "created_by", "contribution_refs",
    "approval_refs", "protocol_ref", "relations", "source_refs",
    "artifact_refs", "payload",
}
RECORD_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*_[A-Za-z0-9._-]+$")
STUDY_ID_PATTERN = re.compile(r"^study_[A-Za-z0-9._-]+$")
ARTIFACT_REF_PATTERN = re.compile(r"^artifact_")
ARTIFACT_REVISION_PATTERN = re.compile(r"^artifact_.*@sha256:[a-f0-9]{64}$")
APPROVAL_REF_PATTERN = re.compile(r"^approval_")
CONTRIBUTION_REF_PATTERN = re.compile(r"^contrib_")
PROTOCOL_REF_PATTERN = re.compile(r"^protocol_")


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


def _matches(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.match(value) is not None


def _is_datetime(value: Any) -> bool:
    if not _is_nonempty_string(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_ref_array(
    record: dict[str, Any], field: str, pattern: re.Pattern[str], issues: list[ValidationIssue]
) -> None:
    value = record.get(field)
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(_issue(record, f"/{field}", "must be an array"))
        return
    if len(value) != len(set(value)):
        issues.append(_issue(record, f"/{field}", "must not contain duplicate values"))
    for index, item in enumerate(value):
        if not _matches(item, pattern):
            issues.append(_issue(record, f"/{field}/{index}", "does not match the required identifier pattern"))


def validate_core_record(record: dict[str, Any], known_ids: set[str] | None = None) -> list[ValidationIssue]:
    """Validate safety-relevant core invariants without external libraries."""

    issues: list[ValidationIssue] = []
    if not isinstance(record, dict):
        return [ValidationIssue("<unknown>", "/", "record must be an object")]
    for field in ("schema_version", "record_id", "record_kind", "occurred_at", "recorded_at", "status"):
        if not _is_nonempty_string(record.get(field)):
            issues.append(_issue(record, f"/{field}", "required non-empty string"))
    for field in sorted(set(record) - CORE_TOP_LEVEL_FIELDS):
        issues.append(_issue(record, f"/{field}", "unexpected property"))
    if record.get("schema_version") != "core/1.0":
        issues.append(_issue(record, "/schema_version", "must be core/1.0"))
    if not _matches(record.get("record_id"), RECORD_ID_PATTERN):
        issues.append(_issue(record, "/record_id", "does not match the required identifier pattern"))
    if record.get("study_id") is not None and not _matches(record.get("study_id"), STUDY_ID_PATTERN):
        issues.append(_issue(record, "/study_id", "does not match the required identifier pattern"))
    for field in ("occurred_at", "recorded_at"):
        if not _is_datetime(record.get(field)):
            issues.append(_issue(record, f"/{field}", "must be an ISO 8601 date-time"))
    if record.get("record_kind") not in CORE_RECORD_KINDS:
        issues.append(_issue(record, "/record_kind", "unknown core record kind"))
    if record.get("status") not in CORE_STATUSES:
        issues.append(_issue(record, "/status", "unknown core status"))

    actor = record.get("created_by")
    if not isinstance(actor, dict):
        issues.append(_issue(record, "/created_by", "must be an object"))
    else:
        if not _matches(actor.get("actor_id"), re.compile(r"^actor_")):
            issues.append(_issue(record, "/created_by/actor_id", "does not match the required identifier pattern"))
        if actor.get("actor_type") not in {"human", "ai", "external_system"}:
            issues.append(_issue(record, "/created_by/actor_type", "must identify human, ai, or external_system"))
        for field in sorted(set(actor) - {"actor_id", "actor_type"}):
            issues.append(_issue(record, f"/created_by/{field}", "unexpected property"))

    payload = record.get("payload")
    if not isinstance(payload, dict):
        issues.append(_issue(record, "/payload", "must be an object"))
        payload = {}

    _validate_ref_array(record, "contribution_refs", CONTRIBUTION_REF_PATTERN, issues)
    _validate_ref_array(record, "approval_refs", APPROVAL_REF_PATTERN, issues)
    _validate_ref_array(record, "artifact_refs", ARTIFACT_REF_PATTERN, issues)
    if record.get("protocol_ref") is not None and not _matches(record.get("protocol_ref"), PROTOCOL_REF_PATTERN):
        issues.append(_issue(record, "/protocol_ref", "does not match the required identifier pattern"))

    relations = record.get("relations", [])
    if not isinstance(relations, list):
        issues.append(_issue(record, "/relations", "must be an array"))
        relations = []
    for index, relation in enumerate(relations):
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
        for field in sorted(set(relation) - {"type", "target_id"}):
            issues.append(_issue(record, f"/relations/{index}/{field}", "unexpected property"))

    source_refs = record.get("source_refs", [])
    if not isinstance(source_refs, list):
        issues.append(_issue(record, "/source_refs", "must be an array"))
        source_refs = []
    for index, evidence in enumerate(source_refs):
        if not isinstance(evidence, dict):
            issues.append(_issue(record, f"/source_refs/{index}", "must be an object"))
            continue
        if not _matches(evidence.get("artifact_revision_id"), ARTIFACT_REVISION_PATTERN):
            issues.append(_issue(record, f"/source_refs/{index}/artifact_revision_id", "does not match the required artifact revision pattern"))
        locator = evidence.get("locator")
        if not isinstance(locator, dict) or not locator:
            issues.append(_issue(record, f"/source_refs/{index}/locator", "must be a non-empty object"))
        if evidence.get("verification_status") not in {"unverified", "integrity_verified", "human_verified"}:
            issues.append(_issue(record, f"/source_refs/{index}/verification_status", "must be unverified, integrity_verified, or human_verified"))
        for field in sorted(set(evidence) - {"artifact_revision_id", "locator", "verification_status"}):
            issues.append(_issue(record, f"/source_refs/{index}/{field}", "unexpected property"))

    if record.get("record_kind") == "execution_session" and record.get("status") == "active":
        if not record.get("approval_refs"):
            issues.append(_issue(record, "/approval_refs", "active session requires explicit approval"))

    if record.get("record_kind") == "claim" and payload.get("support_status") == "supported":
        evidence = record.get("source_refs", [])
        if not any(isinstance(item, dict) and item.get("verification_status") == "human_verified" for item in evidence):
            issues.append(_issue(record, "/source_refs", "supported claim requires human-verified evidence"))

    if record.get("record_kind") == "amendment":
        corrects = [item for item in relations if isinstance(item, dict) and item.get("type") == "corrects"]
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
