"""Adapter from Core 1.0 records to the legacy-compatible search projection."""

from __future__ import annotations

import re
from typing import Any


def is_core_record(record: dict[str, Any]) -> bool:
    return record.get("schema_version") == "core/1.0"


def _summary(record: dict[str, Any]) -> str:
    payload = record.get("payload") or {}
    for key in ("summary", "statement", "objective", "title"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    observed = payload.get("observed")
    if isinstance(observed, dict) and isinstance(observed.get("statement"), str):
        return observed["statement"].strip()
    if isinstance(observed, str) and observed.strip():
        return observed.strip()
    return f"{record.get('record_kind', 'research')} record {record.get('record_id', 'unknown')}"


def _source(record: dict[str, Any]) -> dict[str, Any]:
    for evidence in record.get("source_refs", []):
        if not isinstance(evidence, dict):
            continue
        locator = evidence.get("locator")
        if not isinstance(locator, dict) or not isinstance(locator.get("path"), str):
            continue
        revision = str(evidence.get("artifact_revision_id") or "")
        match = re.search(r"@sha256:([a-f0-9]{64})$", revision)
        start = locator.get("start", locator.get("line_start"))
        end = locator.get("end", locator.get("line_end"))
        if not isinstance(start, int):
            start = None
        if not isinstance(end, int):
            end = None
        return {
            "source_path": locator["path"],
            "source_sha256": match.group(1) if match else None,
            "heading": str(locator.get("heading") or record.get("record_kind") or "Research record"),
            "line_start": start,
            "line_end": end,
            "legacy_import": False,
            "requires_human_review": evidence.get("verification_status") != "human_verified",
        }
    return {}


def core_record_to_index_document(record: dict[str, Any]) -> dict[str, Any]:
    """Project a Core record into fields consumed by existing derived indexes.

    The original Core object remains the raw JSON authority. This adapter exists
    only to keep lexical/semantic retrieval backward-compatible while Core and
    legacy ledgers coexist.
    """

    if not is_core_record(record):
        raise ValueError("core_record_to_index_document requires schema_version core/1.0")
    payload = record.get("payload") or {}
    return {
        "event_id": record["record_id"],
        "date": str(record["occurred_at"])[:10],
        "event_type": record["record_kind"],
        "status": record["status"],
        "project": record.get("study_id") or "unscoped-study",
        "workstream": payload.get("workstream"),
        "summary": _summary(record),
        "relations": [
            {"type": relation.get("type", "unknown"), "target": relation.get("target_id", "unknown")}
            for relation in record.get("relations", [])
            if isinstance(relation, dict)
        ],
        "artifacts": [
            {"path": artifact_ref, "role": "core_artifact_ref"}
            for artifact_ref in record.get("artifact_refs", [])
            if isinstance(artifact_ref, str)
        ],
        "source": _source(record),
        "core_payload": payload,
    }


def index_document(record: dict[str, Any]) -> dict[str, Any]:
    """Return a legacy record unchanged or project a Core record for indexing."""

    return core_record_to_index_document(record) if is_core_record(record) else record


def index_document_id(record: dict[str, Any]) -> str:
    document = index_document(record)
    return str(document["event_id"])


def index_summary_text(record: dict[str, Any]) -> str:
    """Stable retrieval text shared by lexical and semantic builders."""

    document = index_document(record)
    return "\n".join(
        [
            f"Title: {document.get('source', {}).get('heading', document['event_id'])}",
            f"Event type: {document.get('event_type', 'unknown')}",
            f"Status: {document.get('status', 'unknown')}",
            f"Summary: {document.get('summary', '')}",
        ]
    )
