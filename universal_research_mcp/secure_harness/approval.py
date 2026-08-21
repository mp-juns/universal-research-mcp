"""Create-only, one-time approvals for sealed harness plans."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from universal_research_mcp.governance.agent_creation import agent_creation_disclosure_hash
from universal_research_mcp.governance.hashing import hash_without

from .contracts import validate_run_plan


APPROVAL_VERSION = "harness-approval/2.0"
CONSUMPTION_VERSION = "harness-approval-consumption/2.0"
_MAX_BYTES = 64 * 1024
_APPROVAL_FIELDS = frozenset({
    "schema_version",
    "project_root_hash",
    "run_id",
    "run_plan_hash",
    "agent_creation_disclosure_hash",
    "snapshot_hash",
    "image",
    "model",
    "reasoning_effort",
    "workflow_mode",
    "approval_mode",
    "resources",
    "operation_ids",
    "created_at",
    "expires_at",
    "authority_source",
    "one_time",
    "approval_hash",
})
_CONSUMPTION_FIELDS = frozenset({
    "schema_version",
    "run_id",
    "run_plan_hash",
    "agent_creation_disclosure_hash",
    "approval_hash",
    "consumed_at",
    "authority_source",
    "consumption_hash",
})


class HarnessApprovalError(RuntimeError):
    """Raised when exact host-owned approval is unavailable or invalid."""


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise HarnessApprovalError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HarnessApprovalError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise HarnessApprovalError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def project_root_hash(root: str | Path) -> str:
    resolved = Path(root).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise HarnessApprovalError("project root must be a directory")
    return "sha256:" + hashlib.sha256(os.fsencode(str(resolved))).hexdigest()


class HarnessApprovalStore:
    def __init__(
        self,
        project_root: str | Path,
        *,
        state_root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve(strict=True)
        environment = os.environ if environ is None else environ
        selected = Path(state_root) if state_root is not None else Path(
            environment.get("XDG_STATE_HOME", Path.home() / ".local/state")
        )
        if not selected.expanduser().is_absolute():
            raise HarnessApprovalError("host state root must be absolute")
        lexical = Path(os.path.abspath(os.fspath(selected.expanduser())))
        resolved = lexical.resolve(strict=False)
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            pass
        else:
            raise HarnessApprovalError("host state cannot be inside the project")
        if lexical != resolved:
            raise HarnessApprovalError("host state root cannot contain symlinks")
        digest = project_root_hash(self.project_root).removeprefix("sha256:")
        self.state_root = lexical
        self.directory = lexical / "universal-research-mcp" / "harness-approvals" / digest

    def _path(self, run_id: str, suffix: str) -> Path:
        if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in run_id):
            raise HarnessApprovalError("invalid run ID")
        return self.directory / f"{run_id}.{suffix}.json"

    def _plan_binding(self, normalized: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "project_root_hash": project_root_hash(self.project_root),
            "run_id": normalized["run_id"],
            "run_plan_hash": normalized["run_plan_hash"],
            "agent_creation_disclosure_hash": agent_creation_disclosure_hash(
                normalized["agent_creation_disclosure"],
                expected_agent_count=1,
            ),
            "snapshot_hash": normalized["snapshot_hash"],
            "image": normalized["image"],
            "model": normalized["model"],
            "reasoning_effort": normalized["reasoning_effort"],
            "workflow_mode": normalized["workflow_mode"],
            "approval_mode": normalized["approval_mode"],
            "resources": normalized["resources"],
            "operation_ids": [item["operation_id"] for item in normalized["operations"]],
            "authority_source": "explicit_local_cli_approval",
            "one_time": True,
        }

    def create(self, plan: Mapping[str, Any], *, expected_plan_hash: str, expires_at: str) -> dict[str, Any]:
        normalized = validate_run_plan(plan)
        if normalized["project_root_hash"] != project_root_hash(self.project_root):
            raise HarnessApprovalError("plan is bound to another project")
        if normalized["run_plan_hash"] != expected_plan_hash:
            raise HarnessApprovalError("expected plan hash does not match")
        disclosure_hash = agent_creation_disclosure_hash(
            normalized["agent_creation_disclosure"],
            expected_agent_count=1,
        )
        expiry = _timestamp(expires_at, "approval expiry")
        if expiry <= datetime.now(timezone.utc):
            raise HarnessApprovalError("approval expiry must be timezone-qualified and in the future")
        approval = {
            "schema_version": APPROVAL_VERSION,
            **self._plan_binding(normalized),
            "agent_creation_disclosure_hash": disclosure_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expiry.isoformat(),
        }
        approval["approval_hash"] = hash_without(approval, "approval_hash")
        self._create_json(self._path(normalized["run_id"], "grant"), approval)
        return {key: approval[key] for key in (
            "schema_version", "run_id", "run_plan_hash", "expires_at", "approval_hash",
        )}

    def consume(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        normalized = validate_run_plan(plan)
        grant_path = self._path(normalized["run_id"], "grant")
        consumed_path = self._path(normalized["run_id"], "consumed")
        grant = self._read_json(grant_path)
        if set(grant) != _APPROVAL_FIELDS or grant.get("schema_version") != APPROVAL_VERSION:
            raise HarnessApprovalError("approval schema is unsupported")
        if grant.get("approval_hash") != hash_without(grant, "approval_hash"):
            raise HarnessApprovalError("approval integrity hash mismatch")
        expected = self._plan_binding(normalized)
        if any(grant.get(key) != item for key, item in expected.items()):
            raise HarnessApprovalError("approval does not match the exact plan")
        created = _timestamp(grant.get("created_at"), "approval created_at")
        expiry = _timestamp(grant.get("expires_at"), "approval expires_at")
        if expiry <= created:
            raise HarnessApprovalError("approval expiry is not after creation")
        if expiry <= datetime.now(timezone.utc):
            raise HarnessApprovalError("approval has expired")
        consumption = {
            "schema_version": CONSUMPTION_VERSION,
            "run_id": normalized["run_id"],
            "run_plan_hash": normalized["run_plan_hash"],
            "agent_creation_disclosure_hash": expected["agent_creation_disclosure_hash"],
            "approval_hash": grant["approval_hash"],
            "consumed_at": datetime.now(timezone.utc).isoformat(),
            "authority_source": "explicit_local_cli_approval",
        }
        consumption["consumption_hash"] = hash_without(consumption, "consumption_hash")
        try:
            self._create_json(consumed_path, consumption)
        except HarnessApprovalError as exc:
            raise HarnessApprovalError("approval was already consumed or cannot be marked safely") from exc
        return consumption

    def verify_consumed(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        """Verify that exact user approval was consumed before agent creation."""

        normalized = validate_run_plan(plan)
        grant = self._read_json(self._path(normalized["run_id"], "grant"))
        consumption = self._read_json(self._path(normalized["run_id"], "consumed"))
        expected_disclosure_hash = agent_creation_disclosure_hash(
            normalized["agent_creation_disclosure"],
            expected_agent_count=1,
        )
        if (
            set(grant) != _APPROVAL_FIELDS
            or grant.get("schema_version") != APPROVAL_VERSION
            or grant.get("approval_hash") != hash_without(grant, "approval_hash")
        ):
            raise HarnessApprovalError("approval grant is invalid")
        plan_binding = self._plan_binding(normalized)
        if any(grant.get(key) != item for key, item in plan_binding.items()):
            raise HarnessApprovalError("approval grant does not match the exact plan")
        created = _timestamp(grant.get("created_at"), "approval created_at")
        expiry = _timestamp(grant.get("expires_at"), "approval expires_at")
        if expiry <= created:
            raise HarnessApprovalError("approval expiry is not after creation")
        if set(consumption) != _CONSUMPTION_FIELDS:
            raise HarnessApprovalError("approval consumption schema is unsupported")
        expected = {
            "schema_version": CONSUMPTION_VERSION,
            "run_id": normalized["run_id"],
            "run_plan_hash": normalized["run_plan_hash"],
            "agent_creation_disclosure_hash": expected_disclosure_hash,
            "approval_hash": grant.get("approval_hash"),
            "authority_source": "explicit_local_cli_approval",
        }
        if any(consumption.get(key) != item for key, item in expected.items()):
            raise HarnessApprovalError("approval consumption does not match the exact plan")
        if consumption.get("consumption_hash") != hash_without(
            consumption,
            "consumption_hash",
        ):
            raise HarnessApprovalError("approval consumption integrity hash mismatch")
        consumed_at = _timestamp(consumption.get("consumed_at"), "approval consumed_at")
        if consumed_at < created or consumed_at >= expiry:
            raise HarnessApprovalError("approval was not consumed during its valid interval")
        return consumption

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(self._read_bytes(path).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HarnessApprovalError("approval file is unreadable") from exc
        if not isinstance(value, dict):
            raise HarnessApprovalError("approval must be an object")
        return value

    def _create_json(self, path: Path, value: Mapping[str, Any]) -> None:
        if path.parent != self.directory:
            raise HarnessApprovalError("approval artifact escapes its directory")
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > _MAX_BYTES:
            raise HarnessApprovalError("approval artifact is too large")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        directory_descriptor = self._open_directory()
        try:
            descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_descriptor)
        except OSError as exc:
            os.close(directory_descriptor)
            raise HarnessApprovalError("approval artifact already exists or cannot be created") from exc
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = os.write(descriptor, view[offset:])
                if written <= 0:
                    raise HarnessApprovalError("approval artifact write made no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            os.fsync(directory_descriptor)
            os.close(directory_descriptor)

    def _read_bytes(self, path: Path) -> bytes:
        if path.parent != self.directory:
            raise HarnessApprovalError("approval artifact escapes its directory")
        directory_descriptor = self._open_directory()
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path.name, flags, dir_fd=directory_descriptor)
        except OSError as exc:
            os.close(directory_descriptor)
            raise HarnessApprovalError("matching approval is missing or unsafe") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > _MAX_BYTES:
                raise HarnessApprovalError("approval artifact is not a bounded regular file")
            chunks: list[bytes] = []
            remaining = _MAX_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > _MAX_BYTES:
                raise HarnessApprovalError("approval artifact is too large")
            return payload
        finally:
            os.close(descriptor)
            os.close(directory_descriptor)

    def _open_directory(self) -> int:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.directory.anchor, flags)
        except OSError as exc:
            raise HarnessApprovalError("host state filesystem root is unsafe") from exc
        try:
            for component in self.directory.parts[1:]:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
                if not stat.S_ISDIR(os.fstat(child).st_mode):
                    os.close(child)
                    raise HarnessApprovalError("host state path contains a non-directory")
                os.close(descriptor)
                descriptor = child
            return descriptor
        except Exception:
            os.close(descriptor)
            raise
