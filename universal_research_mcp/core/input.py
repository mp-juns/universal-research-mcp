"""Append-only canonical-input validation and persistence helpers.

The management CLI and the explicitly gated MCP ingestion service share these
helpers.  Neither caller may rewrite prior canonical records.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from universal_research_mcp.core.canonical_io import apply_append, canonical_write_lock, prepare_append
from universal_research_mcp.core.ledger import ValidationIssue, read_jsonl, validate_records
from universal_research_mcp.core.proposals import _approval_allows
from universal_research_mcp.runtime import ProjectPaths
from universal_research_mcp.runtime.project_io import ProjectFiles


SOURCE_ID = re.compile(r"^src_[A-Za-z0-9._-]+$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def issues_json(issues: Iterable[ValidationIssue]) -> list[dict[str, str]]:
    return [
        {"record_id": item.record_id, "path": item.path, "message": item.message,
         "severity": item.severity}
        for item in issues
    ]


def all_records(paths: ProjectPaths) -> list[dict[str, Any]]:
    return [
        record
        for ledger in sorted((paths.events_root / "daily").glob("*/events.jsonl"))
        for record in read_jsonl(ProjectFiles(paths.root).path(ledger))
    ]


def read_record_input(path: Path) -> list[dict[str, Any]]:
    """Read either a single JSON object or a JSONL input without modifying it."""

    text = path.read_text(encoding="utf-8")
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(loaded, dict):
        return [loaded]
    if not isinstance(loaded, list) or not all(isinstance(item, dict) for item in loaded):
        raise ValueError("record input must be one JSON object or JSONL objects")
    return loaded


def _registered_sources(paths: ProjectPaths) -> list[dict[str, Any]]:
    manifest = ProjectFiles(paths.root).path(paths.events_root / "sources.jsonl")
    return read_jsonl(manifest) if manifest.is_file() else []


def register_source(
    root: str | Path,
    relative_path: str,
    *,
    source_id: str,
    source_type: str,
) -> dict[str, Any]:
    """Append one new, project-contained source revision to ``sources.jsonl``."""

    paths = ProjectPaths.from_root(root)
    source = paths.resolve_relative(relative_path)
    if not source.is_file():
        raise FileNotFoundError(f"source artifact not found: {relative_path}")
    if not SOURCE_ID.fullmatch(source_id):
        raise ValueError("source_id must begin with src_")
    if not source_type.strip():
        raise ValueError("source_type must be a non-empty string")
    relative = source.relative_to(paths.root).as_posix()
    with canonical_write_lock(paths):
        existing = _registered_sources(paths)
        if any(item.get("source_path") == relative for item in existing):
            raise ValueError("source path is already registered; register a new path for a new revision")
        if any(item.get("source_id") == source_id for item in existing):
            raise ValueError("source_id is already registered")
        record = {
            "source_id": source_id,
            "source_path": relative,
            "source_sha256": _sha256(source),
            "source_type": source_type,
            "legacy_import": False,
        }
        apply_append(paths, prepare_append(
            paths, target=paths.events_root / "sources.jsonl",
            append_value=record, kind="source_registration",
        ))
    return record


def _source_issues(
    paths: ProjectPaths, records: Iterable[dict[str, Any]], sources: list[dict[str, Any]],
) -> list[ValidationIssue]:
    by_path = {str(item.get("source_path")): str(item.get("source_sha256", "")).lower() for item in sources}
    issues: list[ValidationIssue] = []
    for record in records:
        for index, ref in enumerate(record.get("source_refs") or []):
            if not isinstance(ref, dict):
                continue
            raw_locator = ref.get("locator")
            locator = raw_locator if isinstance(raw_locator, dict) else {}
            path = locator.get("path")
            revision = str(ref.get("artifact_revision_id") or "")
            match = re.search(r"@sha256:([a-f0-9]{64})$", revision)
            if not isinstance(path, str) or path not in by_path:
                issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), f"/source_refs/{index}/locator/path", "source path is not registered"))
            elif match and by_path[path] != match.group(1):
                issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), f"/source_refs/{index}/artifact_revision_id", "source hash does not match registered source"))
            elif isinstance(path, str):
                actual_path = paths.resolve_relative(path)
                if not actual_path.is_file() or _sha256(actual_path).lower() != by_path[path]:
                    issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), f"/source_refs/{index}", "registered source content hash mismatch"))
    return issues


def _harness_promotion_issues(root: str | Path, records: Iterable[dict[str, Any]]) -> list[ValidationIssue]:
    """Require a persisted worker attestation for declared governed outcomes."""

    issues: list[ValidationIssue] = []
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        mode = payload.get("workflow_mode")
        if mode is None or mode == "lightweight":
            continue
        identifier = str(record.get("record_id") or "<unknown>")
        if mode not in {"benchmark", "final_review"}:
            issues.append(ValidationIssue(identifier, "/payload/workflow_mode", "workflow_mode is unsupported"))
            continue
        try:
            from universal_research_mcp.secure_harness.controller import promotion_attestation_binding

            binding = promotion_attestation_binding(
                root,
                payload.get("harness_attestation"),
                state_root=os.environ.get("UNIVERSAL_RESEARCH_HARNESS_STATE_ROOT"),
            )
            if binding["workflow_mode"] != mode:
                raise ValueError("harness attestation workflow_mode does not match the canonical record")
        except (OSError, RuntimeError, ValueError) as exc:
            issues.append(ValidationIssue(
                identifier,
                "/payload/harness_attestation",
                f"{mode} canonical promotion requires a valid secure-harness attestation: {exc}",
            ))
    return issues


def validate_candidate_records(root: str | Path, records: list[dict[str, Any]]) -> list[ValidationIssue]:
    return validate_candidate_records_with_sources(root, records, sources=None)


def validate_candidate_records_with_sources(
    root: str | Path,
    records: list[dict[str, Any]],
    *,
    sources: list[dict[str, Any]] | None,
) -> list[ValidationIssue]:
    """Validate records against registered or explicitly staged sources.

    ``sources`` is used by a prepare/commit boundary to validate a future
    append without first mutating the canonical source manifest.  Every staged
    source must still be rechecked before it is actually registered.
    """

    paths = ProjectPaths.from_root(root)
    existing = all_records(paths)
    combined = [*existing, *records]
    issues = validate_records(combined)
    existing_ids = {str(item.get("record_id") or item.get("event_id")) for item in existing}
    candidate_ids: set[str] = set()
    for record in records:
        identifier = str(record.get("record_id") or "")
        if identifier in existing_ids or identifier in candidate_ids:
            issues.append(ValidationIssue(identifier or "<unknown>", "/record_id", "record ID already exists"))
        candidate_ids.add(identifier)
    effective_sources = _registered_sources(paths) if sources is None else sources
    issues.extend(_source_issues(paths, records, effective_sources))
    issues.extend(_harness_promotion_issues(paths.root, records))
    approvals = {str(item.get("record_id")): item for item in [*existing, *records]}
    for record in records:
        if record.get("record_kind") == "approval":
            continue
        for approval_ref in record.get("approval_refs") or []:
            approval = approvals.get(str(approval_ref))
            if approval is None:
                issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), "/approval_refs", "referenced approval record does not exist"))
            else:
                reason = _approval_allows(record, approval)
                if reason:
                    issues.append(ValidationIssue(str(record.get("record_id") or "<unknown>"), "/approval_refs", reason))
    return issues


def ledger_path_for_record(paths: ProjectPaths, record: dict[str, Any]) -> Path:
    value = record.get("recorded_at")
    if not isinstance(value, str):
        raise ValueError("recorded_at must be an ISO 8601 date-time")
    try:
        day = datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError("recorded_at must be an ISO 8601 date-time") from exc
    return paths.events_root / "daily" / day / "events.jsonl"


def append_record(root: str | Path, record: dict[str, Any], *, approval_ref: str | None = None, approval_bootstrap: bool = False) -> Path:
    """Append one validated record; the caller supplies the explicit approval mode."""

    if record.get("record_kind") == "approval":
        if not approval_bootstrap:
            raise ValueError("approval records require record approve")
        actor = record.get("created_by") or {}
        if record.get("status") != "approved" or actor.get("actor_type") != "human":
            raise ValueError("bootstrap approval must be approved and created by a human")
    else:
        if not approval_ref:
            raise ValueError("record append requires --approval-ref")
        if approval_ref not in (record.get("approval_refs") or []):
            raise ValueError("record must carry the explicit --approval-ref")
    paths = ProjectPaths.from_root(root)
    with canonical_write_lock(paths):
        issues = validate_candidate_records(root, [record])
        if issues:
            rendered = "; ".join(f"{item.path}: {item.message}" for item in issues)
            raise ValueError(f"canonical append refused: {rendered}")
        ledger = ledger_path_for_record(paths, record)
        apply_append(paths, prepare_append(
            paths, target=ledger, append_value=record, kind="event_record",
        ))
    return ledger


def sample_record() -> dict[str, Any]:
    """Return a valid, source-free core record template for host editing."""

    return {
        "schema_version": "core/1.0", "record_id": "protocol_example",
        "record_kind": "protocol", "study_id": "study_example",
        "occurred_at": "2026-08-12T00:00:00+00:00",
        "recorded_at": "2026-08-12T00:00:00+00:00", "status": "completed",
        "created_by": {"actor_id": "actor_human", "actor_type": "human"},
        "payload": {"summary": "Replace this template with an approved research protocol."},
    }
