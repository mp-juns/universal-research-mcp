"""Explicit, append-only commit boundary for approved core records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from universal_research_mcp.core.ledger import ValidationIssue, read_jsonl, validate_core_record


def _approval_allows(record: dict[str, Any], approval: dict[str, Any]) -> str | None:
    if approval.get("record_kind") != "approval":
        return "referenced record is not an approval"
    if approval.get("status") != "approved":
        return "referenced approval is not approved"
    if (approval.get("created_by") or {}).get("actor_type") != "human":
        return "approval must be issued by a human actor"
    scope = (approval.get("payload") or {}).get("scope")
    if not isinstance(scope, dict):
        return "approval has no explicit scope"
    record_ids = scope.get("record_ids", [])
    study_ids = scope.get("study_ids", [])
    record_kinds = scope.get("record_kinds", [])
    if record.get("record_id") in record_ids:
        return None
    if record.get("study_id") not in study_ids:
        return "approval scope does not include the record study"
    if record.get("record_kind") not in record_kinds:
        return "approval scope does not include the record kind"
    return None


def validate_commit(record: dict[str, Any], existing_records: list[dict[str, Any]], approval_ref: str) -> list[ValidationIssue]:
    """Validate a proposed core record before an adapter appends it."""

    identifiers = {str(item.get("record_id") or item.get("event_id")) for item in existing_records}
    if record.get("record_id") in identifiers:
        return [ValidationIssue(str(record.get("record_id")), "/record_id", "record ID already exists")]
    issues = validate_core_record(record, identifiers | {str(record.get("record_id") or "")})
    if not approval_ref.startswith("approval_"):
        issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), "/approval_ref", "explicit approval reference is required"))
    elif approval_ref not in record.get("approval_refs", []):
        issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), "/approval_refs", "record must carry the explicit approval reference"))
    else:
        approval = next((item for item in existing_records if item.get("record_id") == approval_ref), None)
        if approval is None:
            issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), "/approval_refs", "referenced approval record does not exist"))
        else:
            reason = _approval_allows(record, approval)
            if reason:
                issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), "/approval_refs", reason))
    if record.get("status") in {"draft", "proposed"}:
        issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), "/status", "drafts and proposals cannot be committed to canonical ledger"))
    return issues


def append_approved_record(ledger_path: Path, record: dict[str, Any], approval_ref: str) -> None:
    """Append one approved core record; never update or rewrite prior lines."""

    existing = read_jsonl(ledger_path) if ledger_path.exists() else []
    issues = validate_commit(record, existing, approval_ref)
    if issues:
        rendered = "; ".join(f"{item.path}: {item.message}" for item in issues)
        raise ValueError(f"commit refused: {rendered}")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
