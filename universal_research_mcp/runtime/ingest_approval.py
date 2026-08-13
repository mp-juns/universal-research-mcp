"""Host-owned, signed one-time approval receipts for MCP ingestion.

The receipt authority lives outside a research project and is never exposed as
an MCP tool.  It is intentionally small: a receipt authorizes one exact pending
draft, not arbitrary record data or a broad write capability.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Mapping

from universal_research_mcp.core.ingest import ingest_approval_binding


RECEIPT_VERSION = "mcp-ingest-approval-receipt/1.0"
CONSUMPTION_VERSION = "mcp-ingest-approval-consumption/1.0"
AUTHORITY_SOURCE = "explicit_local_receipt_authority"
_RECEIPT_ID = re.compile(r"^receipt_[a-f0-9]{24}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_BYTES = 16_384


class IngestApprovalError(RuntimeError):
    """Raised when an external host receipt is absent, stale, or invalid."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _timestamp(value: str, label: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value:
        raise IngestApprovalError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IngestApprovalError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise IngestApprovalError(f"{label} must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    return normalized, normalized.isoformat()


def _project_root(root: str | Path) -> Path:
    try:
        resolved = Path(root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise IngestApprovalError("project root must exist before receipt issuance") from exc
    if not resolved.is_dir():
        raise IngestApprovalError("project root must be a directory")
    return resolved


def _project_hash(root: Path) -> str:
    return hashlib.sha256(os.fsencode(str(root))).hexdigest()


def _state_root(
    project_root: Path, value: str | Path | None, environ: Mapping[str, str] | None,
) -> Path:
    environment = os.environ if environ is None else environ
    selected = Path(value) if value is not None else Path(
        environment.get("XDG_STATE_HOME", Path.home() / ".local/state"),
    )
    selected = selected.expanduser()
    if not selected.is_absolute():
        raise IngestApprovalError("receipt state root must be absolute")
    lexical = Path(os.path.abspath(os.fspath(selected)))
    resolved = lexical.resolve(strict=False)
    try:
        lexical.relative_to(project_root)
        inside = True
    except ValueError:
        inside = False
    if inside or resolved != lexical:
        raise IngestApprovalError("receipt state root must be outside the project and symlink-free")
    return lexical


class IngestApprovalStore:
    """Issue and consume HMAC-signed, project-scoped one-time receipts."""

    def __init__(
        self, root: str | Path, *, state_root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.root = _project_root(root)
        self.project_root_hash = _project_hash(self.root)
        self.state_root = _state_root(self.root, state_root, environ)
        self.base = self.state_root / "universal-research-mcp" / "ingest-receipts"
        self.directory = self.base / self.project_root_hash
        self.key_path = self.base / "receipt-hmac-v1.key"

    def issue(
        self, *, draft_id: str, draft_sha256: str, expires_at: str,
    ) -> dict[str, Any]:
        """Create one external receipt for the exact pending draft binding."""

        binding = ingest_approval_binding(
            self.root, draft_id=draft_id, draft_sha256=draft_sha256,
        )
        expiry, normalized_expiry = _timestamp(expires_at, "expires_at")
        now = datetime.now(timezone.utc)
        if expiry <= now:
            raise IngestApprovalError("receipt expiry must be in the future")
        receipt = {
            "schema_version": RECEIPT_VERSION,
            "receipt_id": f"receipt_{secrets.token_hex(12)}",
            "project_root_hash": self.project_root_hash,
            **binding,
            "issued_at": now.isoformat(),
            "expires_at": normalized_expiry,
            "authority_source": AUTHORITY_SOURCE,
            "one_time": True,
        }
        receipt["signature"] = self._sign(receipt)
        self._create_json(self.receipt_path(receipt["receipt_id"]), receipt)
        return self.summary(receipt, consumed=False)

    def consume(
        self, *, draft_id: str, draft_sha256: str, receipt_id: str,
    ) -> dict[str, Any]:
        """Verify and atomically consume the exact receipt before append."""

        binding = ingest_approval_binding(
            self.root, draft_id=draft_id, draft_sha256=draft_sha256,
        )
        receipt = self._read_receipt(receipt_id)
        expected = {"project_root_hash": self.project_root_hash, **binding}
        for key, value in expected.items():
            if receipt.get(key) != value:
                raise IngestApprovalError(f"receipt does not match {key}")
        expiry, _normalized = _timestamp(str(receipt.get("expires_at") or ""), "expires_at")
        if expiry <= datetime.now(timezone.utc):
            raise IngestApprovalError("receipt has expired")
        marker = {
            "schema_version": CONSUMPTION_VERSION,
            "receipt_id": receipt["receipt_id"],
            "receipt_signature": receipt["signature"],
            **expected,
            "consumed_at": datetime.now(timezone.utc).isoformat(),
            "consume_before_canonical_append": True,
        }
        marker["consumption_signature"] = self._sign(marker)
        try:
            self._create_json(self.consumed_path(receipt["receipt_id"]), marker)
        except FileExistsError as exc:
            raise IngestApprovalError("receipt was already consumed") from exc
        return self.summary(receipt, consumed=True) | {
            "consumption_signature": marker["consumption_signature"],
        }

    def receipt_path(self, receipt_id: str) -> Path:
        return self.directory / f"{self._receipt_id(receipt_id)}.json"

    def consumed_path(self, receipt_id: str) -> Path:
        return self.directory / f"{self._receipt_id(receipt_id)}.consumed.json"

    @staticmethod
    def summary(receipt: Mapping[str, Any], *, consumed: bool) -> dict[str, Any]:
        return {
            key: receipt.get(key)
            for key in (
                "schema_version", "receipt_id", "project_root_hash", "draft_id",
                "draft_sha256", "canonical_head_sha256", "record_id", "issued_at",
                "expires_at", "authority_source", "signature",
            )
        } | {"consumed": consumed, "private_key_exposed": False}

    def _receipt_id(self, value: str) -> str:
        if not isinstance(value, str) or not _RECEIPT_ID.fullmatch(value):
            raise IngestApprovalError("invalid receipt_id")
        return value

    def _sign(self, value: Mapping[str, Any]) -> str:
        unsigned = {key: item for key, item in value.items() if key not in {"signature", "consumption_signature"}}
        return hmac.new(self._key(), _canonical(unsigned), hashlib.sha256).hexdigest()

    def _key(self) -> bytes:
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            metadata = self.base.stat()
            if not stat.S_ISDIR(metadata.st_mode) or self.base.is_symlink():
                raise IngestApprovalError("receipt authority directory is unsafe")
            if self.key_path.exists():
                if self.key_path.is_symlink() or not self.key_path.is_file():
                    raise IngestApprovalError("receipt signing key is unsafe")
                key = self.key_path.read_bytes()
                if len(key) != 32:
                    raise IngestApprovalError("receipt signing key has invalid length")
                return key
            descriptor = os.open(
                self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
            )
            try:
                key = secrets.token_bytes(32)
                os.write(descriptor, key)
                os.fsync(descriptor)
                return key
            finally:
                os.close(descriptor)
        except FileExistsError:
            return self._key()
        except OSError as exc:
            raise IngestApprovalError("receipt signing key is unavailable") from exc

    def _create_json(self, path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.is_symlink() or self.directory.is_symlink() or (
            path.parent.resolve(strict=True) != self.directory.resolve(strict=True)
        ):
            raise IngestApprovalError("receipt path escapes its authority directory")
        payload = _canonical(value) + b"\n"
        if len(payload) > _MAX_BYTES:
            raise IngestApprovalError("receipt artifact is too large")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _read_receipt(self, receipt_id: str) -> dict[str, Any]:
        path = self.receipt_path(receipt_id)
        try:
            if self.directory.is_symlink() or path.is_symlink() or not path.is_file():
                raise IngestApprovalError("receipt is missing or unsafe")
            raw = path.read_bytes()
            if len(raw) < 1 or len(raw) > _MAX_BYTES:
                raise IngestApprovalError("receipt artifact size is invalid")
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IngestApprovalError("receipt is unreadable") from exc
        required = {
            "schema_version", "receipt_id", "project_root_hash", "draft_id",
            "draft_sha256", "canonical_head_sha256", "record_id", "issued_at",
            "expires_at", "authority_source", "one_time", "signature",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise IngestApprovalError("receipt schema is invalid")
        if value.get("schema_version") != RECEIPT_VERSION or value.get("authority_source") != AUTHORITY_SOURCE or value.get("one_time") is not True:
            raise IngestApprovalError("receipt authority is invalid")
        if not all(isinstance(value.get(key), str) for key in required - {"one_time"}):
            raise IngestApprovalError("receipt fields are invalid")
        if not _SHA256.fullmatch(str(value.get("draft_sha256"))) or not _SHA256.fullmatch(str(value.get("canonical_head_sha256"))):
            raise IngestApprovalError("receipt hash fields are invalid")
        if not hmac.compare_digest(str(value["signature"]), self._sign(value)):
            raise IngestApprovalError("receipt signature is invalid")
        issued, _issued = _timestamp(value["issued_at"], "issued_at")
        expiry, _expiry = _timestamp(value["expires_at"], "expires_at")
        if issued >= expiry:
            raise IngestApprovalError("receipt timestamps are invalid")
        return value


__all__ = ["IngestApprovalError", "IngestApprovalStore"]
