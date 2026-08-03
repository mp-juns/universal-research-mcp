"""Explicit, append-only commit boundary for approved core records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.ledger import ValidationIssue, read_jsonl, validate_core_record


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
