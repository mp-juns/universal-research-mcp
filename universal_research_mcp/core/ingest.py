"""Gated, append-only ingestion for the unified MCP surface.

Preparing an ingest stores a content-addressed, immutable *non-canonical*
draft.  Committing it never accepts replacement record data: it rechecks the
draft hash, canonical-head fingerprint, source hashes, and a pre-existing
human approval before one append-only attempt.  The MCP client remains the
authority that decides whether to permit a mutating tool call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any

from universal_research_mcp.core.input import (
    SOURCE_ID,
    _registered_sources,
    _sha256,
    append_record,
    issues_json,
    validate_candidate_records_with_sources,
)
from universal_research_mcp.indexing import canonical_fingerprint, ensure_lexical_index, index_status
from universal_research_mcp.runtime import ProjectPaths
from universal_research_mcp.semantic_runtime import build_configured_semantic_index, configured_backend


DRAFT_SCHEMA = "mcp-ingest-draft/1.0"
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
    root = paths.root / "data" / "ingest-drafts"
    return root / "pending", root / "consumed", paths.root / "data" / "audit"


def _safe_draft_id(draft_id: str) -> str:
    if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
        raise ValueError("invalid ingest draft ID")
    return draft_id


def _create_only_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical_bytes(payload) + b"\n"
    if len(encoded) > MAX_DRAFT_BYTES:
        raise ValueError("ingest draft exceeds the maximum safe size")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ValueError("immutable ingest artifact already exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise


def _load_draft(paths: ProjectPaths, draft_id: str) -> tuple[dict[str, Any], Path]:
    pending, _consumed, _audit = _draft_roots(paths)
    path = pending / f"{_safe_draft_id(draft_id)}.json"
    if not path.is_file() or path.is_symlink():
        raise ValueError("pending ingest draft was not found")
    raw = path.read_bytes()
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
    if (consumed / f"{safe_id}.json").exists():
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
    audit_root.mkdir(parents=True, exist_ok=True)
    path = audit_root / "ingest-events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


@contextmanager
def _commit_lock(paths: ProjectPaths):
    """Refuse concurrent commits instead of accepting an unbound race."""

    pending, _consumed, _audit = _draft_roots(paths)
    pending.mkdir(parents=True, exist_ok=True)
    lock = pending / ".commit.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("another ingest commit is active; retry only after it ends") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "started_at": _now()}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        lock.unlink(missing_ok=True)


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
    _create_only_json(pending / f"{draft['draft_id']}.json", draft)
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


def _commit_ingest_locked(
    paths: ProjectPaths,
    *,
    draft_id: str,
    draft_sha256: str,
    approval_receipt_id: str,
) -> dict[str, Any]:
    """Consume exactly one prepared draft and append it after all rechecks."""

    _pending, consumed, _audit = _draft_roots(paths)
    safe_id = _safe_draft_id(draft_id)
    if (consumed / f"{safe_id}.json").exists():
        raise ValueError("ingest draft was already consumed and cannot be replayed")
    draft, _pending_path = _load_draft(paths, draft_id)
    if not isinstance(draft_sha256, str) or draft_sha256 != draft["draft_sha256"]:
        raise ValueError("draft_sha256 does not match the immutable pending draft")
    record = draft.get("record")
    registrations = draft.get("source_registrations")
    approval_ref = draft.get("approval_ref")
    if not isinstance(record, dict) or not isinstance(registrations, list) or not isinstance(approval_ref, str):
        raise ValueError("pending ingest draft has an invalid schema")
    before = canonical_fingerprint(paths.events_root)
    expected = draft.get("canonical_head")
    if not isinstance(expected, dict) or before.get("sha256") != expected.get("sha256"):
        raise ValueError("canonical ledger changed after preparation; prepare a fresh draft")
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
    _validate_draft_record(paths, record, approval_ref, registrations)

    from universal_research_mcp.runtime.ingest_approval import IngestApprovalStore

    receipt = IngestApprovalStore(
        paths.root,
        state_root=os.environ.get("UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT"),
    ).consume(
        draft_id=draft_id,
        draft_sha256=draft_sha256,
        receipt_id=approval_receipt_id,
    )

    consumption = {
        "schema_version": "mcp-ingest-consumption/1.0",
        "draft_id": draft_id,
        "draft_sha256": draft_sha256,
        "approval_receipt_id": approval_receipt_id,
        "consumed_at": _now(),
        "record_id": record.get("record_id"),
    }
    _create_only_json(consumed / f"{draft_id}.json", consumption)
    registered: list[dict[str, Any]] = []
    try:
        from universal_research_mcp.core.input import register_source

        for source in registrations:
            registered.append(register_source(
                paths.root,
                str(source["path"]),
                source_id=str(source["source_id"]),
                source_type=str(source["source_type"]),
            ))
        ledger = append_record(paths.root, record, approval_ref=approval_ref)
        refresh = _refresh_derived_indexes(paths.root)
    except Exception as exc:
        _append_audit(paths, {
            "timestamp": _now(), "event_type": "ingest_commit_failed",
            "draft_id": draft_id, "draft_sha256": draft_sha256,
            "record_id": record.get("record_id"), "authority_basis": "host_mutating_tool",
            "error_type": type(exc).__name__, "reason": str(exc),
        })
        raise
    after = canonical_fingerprint(paths.events_root)
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
        "canonical_head_before": before["sha256"],
        "canonical_head_after": after["sha256"],
        "derived_refresh": refresh,
        "authority_basis": "host_mutating_tool_signed_receipt_and_preexisting_human_scope_approval",
    }
    _append_audit(paths, {
        "timestamp": _now(), "event_type": "ingest_committed",
        "draft_id": draft_id, "draft_sha256": draft_sha256,
        "record_id": record.get("record_id"), "approval_ref": approval_ref,
        "approval_receipt_id": approval_receipt_id,
        "approval_receipt_signature": receipt["signature"],
        "canonical_head_before": before["sha256"], "canonical_head_after": after["sha256"],
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
    return {
        "status": "consumed" if consumed_path.exists() else "pending",
        "draft_id": draft_id,
        "draft_sha256": draft["draft_sha256"],
        "record_id": (draft.get("record") or {}).get("record_id"),
        "prepared_at": draft.get("prepared_at"),
        "canonical_head_sha256": (draft.get("canonical_head") or {}).get("sha256"),
        "pending_path": (pending / f"{draft_id}.json").relative_to(paths.root).as_posix(),
    }


__all__ = [
    "commit_ingest", "ingest_approval_binding", "pending_ingest_status",
    "prepare_ingest",
]
