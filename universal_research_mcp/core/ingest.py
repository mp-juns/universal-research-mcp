"""Gated, append-only ingestion for the unified MCP surface.

Preparing an ingest stores a content-addressed, immutable *non-canonical*
draft.  Committing it never accepts replacement record data: it rechecks the
draft hash, canonical-head fingerprint, source hashes, and a pre-existing
human approval before one append-only attempt.  The MCP client remains the
authority that decides whether to permit a mutating tool call.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any

from universal_research_mcp.core.canonical_io import (
    append_state as _transaction_operation_state,
    apply_append as _apply_transaction_operation,
    canonical_write_lock as _commit_lock,
    prepare_append as _operation,
)
from universal_research_mcp.core.input import (
    SOURCE_ID,
    _registered_sources,
    _sha256,
    issues_json,
    ledger_path_for_record,
    validate_candidate_records_with_sources,
)
from universal_research_mcp.indexing import canonical_fingerprint, ensure_lexical_index, index_status
from universal_research_mcp.runtime import ProjectPaths
from universal_research_mcp.runtime.project_io import ProjectFiles
from universal_research_mcp.semantic_runtime import build_configured_semantic_index, configured_backend


DRAFT_SCHEMA = "mcp-ingest-draft/1.0"
TRANSACTION_SCHEMA = "mcp-ingest-transaction/1.0"
MAX_DRAFT_BYTES = 1_048_576
_DRAFT_ID = re.compile(r"^ingest_[a-f0-9]{24}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _draft_roots(paths: ProjectPaths) -> tuple[Path, Path, Path]:
    # Check every ingest directory before even the first pending/lock write.
    _ = paths.ingest_transactions
    return paths.ingest_pending, paths.ingest_consumed, paths.ingest_audit


def _transaction_path(paths: ProjectPaths, draft_id: str) -> Path:
    return paths.ingest_transactions / f"{_safe_draft_id(draft_id)}.json"


def _safe_draft_id(draft_id: str) -> str:
    if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
        raise ValueError("invalid ingest draft ID")
    return draft_id


def _create_only_json(paths: ProjectPaths, path: Path, payload: dict[str, Any]) -> None:
    encoded = _canonical_bytes(payload) + b"\n"
    if len(encoded) > MAX_DRAFT_BYTES:
        raise ValueError("ingest draft exceeds the maximum safe size")
    try:
        ProjectFiles(paths.root).create(path, encoded)
    except FileExistsError as exc:
        raise ValueError("immutable ingest artifact already exists") from exc


def _replace_json(paths: ProjectPaths, path: Path, payload: dict[str, Any]) -> None:
    """Durably replace a mutable transaction journal in its own directory."""

    encoded = _canonical_bytes(payload) + b"\n"
    if len(encoded) > MAX_DRAFT_BYTES:
        raise ValueError("ingest transaction exceeds the maximum safe size")
    ProjectFiles(paths.root).replace(path, encoded)


def _load_transaction(paths: ProjectPaths, draft_id: str) -> dict[str, Any] | None:
    path = _transaction_path(paths, draft_id)
    raw = ProjectFiles(paths.root).read(path, max_bytes=MAX_DRAFT_BYTES)
    if raw is None:
        return None
    if len(raw) < 1 or len(raw) > MAX_DRAFT_BYTES:
        raise ValueError("ingest transaction journal size is invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("ingest transaction journal is invalid JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != TRANSACTION_SCHEMA:
        raise ValueError("ingest transaction journal schema is invalid")
    declared = value.pop("transaction_sha256", None)
    if not isinstance(declared, str) or declared != _digest(value):
        raise ValueError("ingest transaction journal integrity check failed")
    value["transaction_sha256"] = declared
    return value


def _validate_transaction_binding(
    transaction: dict[str, Any], *, draft_id: str, draft_sha256: str,
    approval_receipt_id: str,
) -> None:
    expected = {
        "draft_id": draft_id,
        "draft_sha256": draft_sha256,
        "approval_receipt_id": approval_receipt_id,
    }
    for key, value in expected.items():
        if transaction.get(key) != value:
            raise ValueError(f"ingest transaction does not match {key}")
    operations = transaction.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("ingest transaction has no canonical operations")


def _load_draft(paths: ProjectPaths, draft_id: str) -> tuple[dict[str, Any], Path]:
    pending, _consumed, _audit = _draft_roots(paths)
    path = pending / f"{_safe_draft_id(draft_id)}.json"
    raw = ProjectFiles(paths.root).read(path, max_bytes=MAX_DRAFT_BYTES)
    if raw is None:
        raise ValueError("pending ingest draft was not found")
    if len(raw) > MAX_DRAFT_BYTES:
        raise ValueError("pending ingest draft exceeds the maximum safe size")
    try:
        draft = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("pending ingest draft is not valid JSON") from exc
    if not isinstance(draft, dict):
        raise ValueError("pending ingest draft is not an object")
    declared = draft.pop("draft_sha256", None)
    if not isinstance(declared, str) or declared != _digest(draft):
        raise ValueError("pending ingest draft integrity check failed")
    draft["draft_sha256"] = declared
    return draft, path


def ingest_approval_binding(
    root: str | Path, *, draft_id: str, draft_sha256: str,
) -> dict[str, str]:
    """Return the exact immutable binding a host receipt may authorize.

    This is deliberately metadata-only: it never returns a draft record body
    and it refuses a draft that has already been consumed.
    """

    paths = ProjectPaths.from_root(root)
    _pending, consumed, _audit = _draft_roots(paths)
    safe_id = _safe_draft_id(draft_id)
    if ProjectFiles(paths.root).exists(consumed / f"{safe_id}.json"):
        raise ValueError("ingest draft was already consumed and cannot be approved")
    draft, _path = _load_draft(paths, safe_id)
    if not isinstance(draft_sha256, str) or draft_sha256 != draft["draft_sha256"]:
        raise ValueError("draft_sha256 does not match the immutable pending draft")
    head = draft.get("canonical_head")
    record = draft.get("record")
    if not isinstance(head, dict) or not isinstance(head.get("sha256"), str):
        raise ValueError("pending ingest draft canonical binding is invalid")
    if not isinstance(record, dict) or not isinstance(record.get("record_id"), str):
        raise ValueError("pending ingest draft record binding is invalid")
    return {
        "draft_id": safe_id,
        "draft_sha256": draft["draft_sha256"],
        "canonical_head_sha256": head["sha256"],
        "record_id": record["record_id"],
    }


def _normalize_source_registrations(
    paths: ProjectPaths,
    source_registrations: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    if source_registrations is None:
        return []
    if not isinstance(source_registrations, list):
        raise ValueError("source_registrations must be an array")
    existing = _registered_sources(paths)
    existing_paths = {str(item.get("source_path")) for item in existing}
    existing_ids = {str(item.get("source_id")) for item in existing}
    pending_paths: set[str] = set()
    pending_ids: set[str] = set()
    normalized: list[dict[str, str]] = []
    for index, source in enumerate(source_registrations):
        if not isinstance(source, dict):
            raise ValueError(f"source_registrations[{index}] must be an object")
        unexpected = sorted(set(source) - {"path", "source_id", "source_type"})
        if unexpected:
            raise ValueError(f"source_registrations[{index}] has unsupported fields")
        raw_path = source.get("path")
        source_id = source.get("source_id")
        source_type = source.get("source_type")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"source_registrations[{index}].path is required")
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ValueError(f"source_registrations[{index}].source_id must begin with src_")
        if not isinstance(source_type, str) or not source_type.strip():
            raise ValueError(f"source_registrations[{index}].source_type is required")
        resolved = paths.resolve_relative(raw_path)
        if not resolved.is_file():
            raise FileNotFoundError(f"source artifact not found: {raw_path}")
        relative = resolved.relative_to(paths.root).as_posix()
        if relative in existing_paths or relative in pending_paths:
            raise ValueError("source path is already registered or staged; use a new immutable revision path")
        if source_id in existing_ids or source_id in pending_ids:
            raise ValueError("source_id is already registered or staged")
        pending_paths.add(relative)
        pending_ids.add(source_id)
        normalized.append({
            "path": relative,
            "source_id": source_id,
            "source_type": source_type.strip(),
            "source_sha256": _sha256(resolved),
        })
    return normalized


def _validate_draft_record(
    paths: ProjectPaths,
    record: dict[str, Any],
    approval_ref: str,
    registrations: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    if record.get("record_kind") == "approval":
        raise ValueError("MCP ingestion cannot create approval records")
    if not isinstance(approval_ref, str) or not approval_ref:
        raise ValueError("approval_ref is required")
    if approval_ref not in (record.get("approval_refs") or []):
        raise ValueError("record must include the explicit approval_ref")
    sources = [*_registered_sources(paths), *[
        {
            "source_id": item["source_id"],
            "source_path": item["path"],
            "source_sha256": item["source_sha256"],
            "source_type": item["source_type"],
            "legacy_import": False,
        }
        for item in registrations
    ]]
    issues = validate_candidate_records_with_sources(paths.root, [record], sources=sources)
    if issues:
        rendered = "; ".join(f"{item.path}: {item.message}" for item in issues)
        raise ValueError(f"ingest preparation refused: {rendered}")
    return issues_json(issues)


def _append_audit(paths: ProjectPaths, event: dict[str, Any]) -> None:
    _pending, _consumed, audit_root = _draft_roots(paths)
    path = audit_root / "ingest-events.jsonl"
    files = ProjectFiles(paths.root)
    before = files.read(path)
    payload = (before or b"") + (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    files.replace(path, payload, expected=before, check_expected=True)


def _append_audit_safely(paths: ProjectPaths, event: dict[str, Any]) -> dict[str, str] | None:
    """Preserve the primary commit/failure outcome if audit storage is unavailable."""

    try:
        _append_audit(paths, event)
    except (OSError, RuntimeError, ValueError) as exc:
        return {"error_type": type(exc).__name__, "reason": str(exc)}
    return None


def _refresh_derived_indexes(root: Path) -> dict[str, Any]:
    """Refresh derived views after a successful canonical append only."""

    try:
        lexical = ensure_lexical_index(root)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "lexical": {"status": "stale", "reason": str(exc), "current": index_status(root)},
            "semantic": {"status": "not_attempted"},
        }
    backend = configured_backend(root)
    if backend is None:
        semantic: dict[str, Any] = {"status": "unconfigured", "executed": False}
    elif not backend.auto_refresh:
        semantic = {
            "status": "stale", "executed": False,
            "reason": "configured semantic refresh requires explicit build",
        }
    else:
        try:
            semantic = build_configured_semantic_index(root)
        except (OSError, RuntimeError, ValueError) as exc:
            semantic = {"status": "stale", "executed": False, "reason": str(exc)}
    return {"lexical": lexical, "semantic": semantic}


def prepare_ingest(
    root: str | Path,
    *,
    record: dict[str, Any],
    approval_ref: str,
    source_registrations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist an immutable pending draft after pure validation only."""

    paths = ProjectPaths.from_root(root)
    registrations = _normalize_source_registrations(paths, source_registrations)
    _validate_draft_record(paths, record, approval_ref, registrations)
    head = canonical_fingerprint(paths.events_root)
    draft = {
        "schema_version": DRAFT_SCHEMA,
        "draft_id": f"ingest_{secrets.token_hex(12)}",
        "prepared_at": _now(),
        "canonical_head": head,
        "approval_ref": approval_ref,
        "record": record,
        "source_registrations": registrations,
    }
    draft["draft_sha256"] = _digest(draft)
    pending, _consumed, _audit = _draft_roots(paths)
    _create_only_json(paths, pending / f"{draft['draft_id']}.json", draft)
    return {
        "status": "prepared",
        "draft_id": draft["draft_id"],
        "draft_sha256": draft["draft_sha256"],
        "record_id": record.get("record_id"),
        "canonical_head_sha256": head["sha256"],
        "source_registration_count": len(registrations),
        "canonical_append": False,
        "commit_requires": [
            "a host-approved mutating research_commit_ingest tool call",
            "the exact draft_id, draft_sha256, and one-time approval_receipt_id",
            "an unchanged canonical head and source files",
            "a signed host receipt and the referenced pre-existing human approval scope",
        ],
    }


def _source_registration_record(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source["source_id"],
        "source_path": source["path"],
        "source_sha256": source["source_sha256"],
        "source_type": source["source_type"],
        "legacy_import": False,
    }


def _prepare_transaction(
    paths: ProjectPaths,
    *,
    draft_id: str,
    draft_sha256: str,
    approval_receipt_id: str,
    record: dict[str, Any],
    registrations: list[dict[str, Any]],
    canonical_head_before: str,
) -> dict[str, Any]:
    operations = []
    if registrations:
        operations.append(_operation(
            paths,
            target=paths.events_root / "sources.jsonl",
            append_value=[_source_registration_record(source) for source in registrations],
            kind="source_registration",
        ))
    operations.append(_operation(
        paths,
        target=ledger_path_for_record(paths, record),
        append_value=record,
        kind="event_record",
    ))
    transaction = {
        "schema_version": TRANSACTION_SCHEMA,
        "status": "prepared",
        "draft_id": draft_id,
        "draft_sha256": draft_sha256,
        "approval_receipt_id": approval_receipt_id,
        "record_id": record.get("record_id"),
        "canonical_head_before": canonical_head_before,
        "prepared_at": _now(),
        "updated_at": _now(),
        "operations": operations,
        "applied_operation_count": 0,
        "last_error": None,
    }
    transaction["transaction_sha256"] = _digest(transaction)
    _create_only_json(paths, _transaction_path(paths, draft_id), transaction)
    return transaction


def _store_transaction(paths: ProjectPaths, transaction: dict[str, Any]) -> None:
    transaction["updated_at"] = _now()
    transaction.pop("transaction_sha256", None)
    transaction["transaction_sha256"] = _digest(transaction)
    _replace_json(paths, _transaction_path(paths, str(transaction["draft_id"])), transaction)


def _commit_ingest_locked(
    paths: ProjectPaths,
    *,
    draft_id: str,
    draft_sha256: str,
    approval_receipt_id: str,
) -> dict[str, Any]:
    """Apply or resume one exact write-ahead canonical transaction."""

    _pending, consumed, _audit = _draft_roots(paths)
    safe_id = _safe_draft_id(draft_id)
    if ProjectFiles(paths.root).exists(consumed / f"{safe_id}.json"):
        raise ValueError("ingest draft was already consumed and cannot be replayed")
    draft, _pending_path = _load_draft(paths, draft_id)
    if not isinstance(draft_sha256, str) or draft_sha256 != draft["draft_sha256"]:
        raise ValueError("draft_sha256 does not match the immutable pending draft")
    record = draft.get("record")
    registrations = draft.get("source_registrations")
    approval_ref = draft.get("approval_ref")
    if not isinstance(record, dict) or not isinstance(registrations, list) or not isinstance(approval_ref, str):
        raise ValueError("pending ingest draft has an invalid schema")
    for source in registrations:
        if not isinstance(source, dict):
            raise ValueError("pending source registration is invalid")
        path = source.get("path")
        expected_sha = source.get("source_sha256")
        if not isinstance(path, str) or not isinstance(expected_sha, str):
            raise ValueError("pending source registration is invalid")
        actual = _sha256(paths.resolve_relative(path))
        if actual != expected_sha:
            raise ValueError("source content changed after preparation; prepare a fresh draft")

    from universal_research_mcp.runtime.ingest_approval import (
        IngestApprovalError,
        IngestApprovalStore,
    )

    store = IngestApprovalStore(
        paths.root,
        state_root=os.environ.get("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT"),
    )
    store.verify(
        draft_id=draft_id,
        draft_sha256=draft_sha256,
        receipt_id=approval_receipt_id,
    )

    transaction = _load_transaction(paths, draft_id)
    if transaction is None:
        before = canonical_fingerprint(paths.events_root)
        expected = draft.get("canonical_head")
        if not isinstance(expected, dict) or before.get("sha256") != expected.get("sha256"):
            raise ValueError("canonical ledger changed after preparation; prepare a fresh draft")
        _validate_draft_record(paths, record, approval_ref, registrations)
        transaction = _prepare_transaction(
            paths,
            draft_id=draft_id,
            draft_sha256=draft_sha256,
            approval_receipt_id=approval_receipt_id,
            record=record,
            registrations=registrations,
            canonical_head_before=str(before["sha256"]),
        )
    else:
        _validate_transaction_binding(
            transaction,
            draft_id=draft_id,
            draft_sha256=draft_sha256,
            approval_receipt_id=approval_receipt_id,
        )
        for operation in transaction["operations"]:
            if not isinstance(operation, dict):
                raise ValueError("ingest transaction operation is invalid")
            _transaction_operation_state(paths, operation)

    try:
        receipt = store.consume(
            draft_id=draft_id,
            draft_sha256=draft_sha256,
            receipt_id=approval_receipt_id,
        )
    except IngestApprovalError as exc:
        if "already consumed" not in str(exc):
            raise
        receipt = store.resume(
            draft_id=draft_id,
            draft_sha256=draft_sha256,
            receipt_id=approval_receipt_id,
        )

    transaction["status"] = "applying"
    transaction["receipt_signature"] = receipt["signature"]
    transaction["last_error"] = None
    _store_transaction(paths, transaction)
    try:
        applied = 0
        for operation in transaction["operations"]:
            if not isinstance(operation, dict):
                raise ValueError("ingest transaction operation is invalid")
            _apply_transaction_operation(paths, operation)
            applied += 1
            transaction["applied_operation_count"] = applied
            _store_transaction(paths, transaction)
        after = canonical_fingerprint(paths.events_root)
        transaction["canonical_head_after"] = after["sha256"]
        transaction["status"] = "canonical_committed"
        _store_transaction(paths, transaction)

        consumption = {
            "schema_version": "mcp-ingest-consumption/1.1",
            "draft_id": draft_id,
            "draft_sha256": draft_sha256,
            "approval_receipt_id": approval_receipt_id,
            "consumed_at": _now(),
            "record_id": record.get("record_id"),
            "transaction_path": _transaction_path(paths, draft_id).relative_to(paths.root).as_posix(),
            "canonical_head_after": after["sha256"],
        }
        _create_only_json(paths, consumed / f"{draft_id}.json", consumption)
    except Exception as exc:
        transaction["status"] = "failed_recoverable"
        transaction["last_error"] = {
            "error_type": type(exc).__name__, "reason": str(exc), "recorded_at": _now(),
        }
        _store_transaction(paths, transaction)
        _append_audit_safely(paths, {
            "timestamp": _now(), "event_type": "ingest_commit_failed_recoverable",
            "draft_id": draft_id, "draft_sha256": draft_sha256,
            "record_id": record.get("record_id"), "authority_basis": "host_mutating_tool",
            "error_type": type(exc).__name__, "reason": str(exc),
            "applied_operation_count": transaction.get("applied_operation_count"),
            "transaction_status": transaction["status"],
        })
        raise

    # The immutable consumption marker is the terminal commit authority. A
    # journal-finalization write that fails after this point must not turn an
    # already committed canonical append into an unretryable reported failure.
    journal_warning: dict[str, str] | None = None
    transaction["status"] = "committed"
    transaction["committed_at"] = _now()
    try:
        _store_transaction(paths, transaction)
    except (OSError, RuntimeError, ValueError) as exc:
        transaction["status"] = "committed_journal_finalization_pending"
        journal_warning = {
            "error_type": type(exc).__name__,
            "reason": str(exc),
        }
        journal_audit_warning = _append_audit_safely(paths, {
            "timestamp": _now(),
            "event_type": "ingest_commit_journal_finalization_pending",
            "draft_id": draft_id,
            "draft_sha256": draft_sha256,
            "record_id": record.get("record_id"),
            "authority_basis": "immutable_consumption_marker_and_verified_canonical_state",
            **journal_warning,
        })
        if journal_audit_warning is not None:
            journal_warning["audit_error"] = json.dumps(
                journal_audit_warning, ensure_ascii=False, sort_keys=True,
            )

    refresh = _refresh_derived_indexes(paths.root)
    registered = [_source_registration_record(source) for source in registrations]
    ledger = ledger_path_for_record(paths, record)
    result = {
        "status": "committed",
        "canonical_append": True,
        "draft_id": draft_id,
        "draft_sha256": draft_sha256,
        "record_id": record.get("record_id"),
        "approval_ref": approval_ref,
        "approval_receipt": receipt,
        "ledger_path": ledger.relative_to(paths.root).as_posix(),
        "registered_sources": registered,
        "canonical_head_before": transaction["canonical_head_before"],
        "canonical_head_after": transaction["canonical_head_after"],
        "transaction_status": transaction["status"],
        "transaction_journal_warning": journal_warning,
        "derived_refresh": refresh,
        "authority_basis": "host_mutating_tool_signed_receipt_and_preexisting_human_scope_approval",
    }
    result["audit_warning"] = _append_audit_safely(paths, {
        "timestamp": _now(), "event_type": "ingest_committed",
        "draft_id": draft_id, "draft_sha256": draft_sha256,
        "record_id": record.get("record_id"), "approval_ref": approval_ref,
        "approval_receipt_id": approval_receipt_id,
        "approval_receipt_signature": receipt["signature"],
        "canonical_head_before": transaction["canonical_head_before"],
        "canonical_head_after": transaction["canonical_head_after"],
        "authority_basis": result["authority_basis"],
        "lexical_status": refresh["lexical"].get("status"),
        "semantic_status": refresh["semantic"].get("status"),
    })
    return result


def commit_ingest(
    root: str | Path,
    *,
    draft_id: str,
    draft_sha256: str,
    approval_receipt_id: str,
) -> dict[str, Any]:
    """Consume exactly one prepared draft under a project-local commit lock."""

    paths = ProjectPaths.from_root(root)
    _draft_roots(paths)
    with _commit_lock(paths):
        return _commit_ingest_locked(
            paths,
            draft_id=draft_id,
            draft_sha256=draft_sha256,
            approval_receipt_id=approval_receipt_id,
        )


def pending_ingest_status(root: str | Path, *, draft_id: str) -> dict[str, Any]:
    """Return only metadata for one immutable pending draft."""

    paths = ProjectPaths.from_root(root)
    draft, _path = _load_draft(paths, draft_id)
    pending, consumed, _audit = _draft_roots(paths)
    consumed_path = consumed / f"{draft_id}.json"
    transaction = _load_transaction(paths, draft_id)
    transaction_status = transaction.get("status") if transaction else None
    if ProjectFiles(paths.root).exists(consumed_path):
        status = "consumed"
    elif transaction_status in {"applying", "canonical_committed", "failed_recoverable"}:
        status = "recovery_required"
    else:
        status = "pending"
    return {
        "status": status,
        "draft_id": draft_id,
        "draft_sha256": draft["draft_sha256"],
        "record_id": (draft.get("record") or {}).get("record_id"),
        "prepared_at": draft.get("prepared_at"),
        "canonical_head_sha256": (draft.get("canonical_head") or {}).get("sha256"),
        "pending_path": (pending / f"{draft_id}.json").relative_to(paths.root).as_posix(),
        "transaction_status": transaction_status,
        "applied_operation_count": (
            transaction.get("applied_operation_count") if transaction else 0
        ),
    }


__all__ = [
    "commit_ingest", "ingest_approval_binding", "pending_ingest_status",
    "prepare_ingest",
]
