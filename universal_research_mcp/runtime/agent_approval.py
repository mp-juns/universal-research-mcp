"""Create-only, one-time execution grants for provider-backed agent runs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from universal_research_mcp.governance.hashing import artifact_hash, canonical_json, hash_without


GRANT_VERSION = "agent-execution-approval/2.0"
CONSUMPTION_VERSION = "agent-execution-approval-consumption/2.0"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_APPROVAL_BYTES = 64 * 1024
_GRANT_FIELDS = frozenset({
    "schema_version",
    "project_root_hash",
    "run_id",
    "run_plan_hash",
    "configuration_hash",
    "estimate_snapshot_hash",
    "execution_request_hash",
    "provider_id",
    "model",
    "network_scope",
    "provider_configuration_hash",
    "approval_ref",
    "budgets",
    "created_at",
    "expires_at",
    "authority_source",
    "one_time",
    "grant_hash",
})
_AUTHORITY_SOURCE = "explicit_local_cli_approval"
_ARTIFACT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


class AgentApprovalError(RuntimeError):
    """Raised when a user-created execution grant is missing or mismatched."""


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or ".." in value:
        raise AgentApprovalError(f"invalid {label}")
    return value


def _utc_timestamp(value: str, label: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value:
        raise AgentApprovalError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentApprovalError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise AgentApprovalError(f"{label} must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    return normalized, normalized.isoformat()


def _configuration_dict(configuration: object) -> dict[str, Any]:
    converter = getattr(configuration, "to_dict", None)
    if not callable(converter):
        raise AgentApprovalError("runtime configuration is not serializable")
    value = converter()
    if not isinstance(value, dict):
        raise AgentApprovalError("runtime configuration must serialize to an object")
    return value


def _exact_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ARTIFACT_HASH.fullmatch(value):
        raise AgentApprovalError(f"{label} must be one exact sha256 artifact hash")
    return value


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _project_root(root: str | Path) -> Path:
    try:
        resolved = Path(root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AgentApprovalError("project root must exist before approval") from exc
    if not resolved.is_dir():
        raise AgentApprovalError("project root must be a directory")
    return resolved


def _project_root_hash(root: Path) -> str:
    return "sha256:" + hashlib.sha256(os.fsencode(str(root))).hexdigest()


def _host_state_root(
    project_root: Path,
    value: str | Path | None,
    environ: Mapping[str, str] | None,
) -> Path:
    environment = os.environ if environ is None else environ
    if value is None:
        xdg_state = environment.get("XDG_STATE_HOME")
        selected = Path(xdg_state) if xdg_state else Path.home() / ".local/state"
    else:
        selected = Path(value)
    selected = selected.expanduser()
    if not selected.is_absolute():
        raise AgentApprovalError("approval host state root must be absolute")
    lexical = Path(os.path.abspath(os.fspath(selected)))
    resolved = lexical.resolve(strict=False)
    if _inside(lexical, project_root) or _inside(resolved, project_root):
        raise AgentApprovalError("approval host state root cannot be inside the project")
    if resolved != lexical:
        raise AgentApprovalError("approval host state root cannot contain symlinks")
    return lexical


def _exact_binding(
    run_plan: Mapping[str, Any],
    configuration: object,
    project_root_hash: str,
    estimate_snapshot_hash: str,
    execution_request_hash: str,
) -> dict[str, Any]:
    config = _configuration_dict(configuration)
    plan_hash = run_plan.get("run_plan_hash")
    if not isinstance(plan_hash, str) or plan_hash != hash_without(dict(run_plan), "run_plan_hash"):
        raise AgentApprovalError("run plan hash is missing or invalid")
    if run_plan.get("configuration") != config:
        raise AgentApprovalError("run plan and runtime configuration differ")
    if run_plan.get("configuration_hash") != artifact_hash(config):
        raise AgentApprovalError("runtime configuration hash is invalid")
    budgets = config.get("budgets")
    if not isinstance(budgets, dict):
        raise AgentApprovalError("runtime budget snapshot is missing")
    required = {
        "provider_id": config.get("provider_id"),
        "model": config.get("model"),
        "network_scope": config.get("network_scope"),
        "provider_configuration_hash": config.get("provider_configuration_hash"),
        "approval_ref": config.get("approval_ref"),
    }
    if not all(isinstance(value, str) and value for value in required.values()):
        raise AgentApprovalError("runtime route binding is incomplete")
    return {
        "project_root_hash": project_root_hash,
        "run_id": _identifier(run_plan.get("run_id"), "run_id"),
        "run_plan_hash": plan_hash,
        "configuration_hash": run_plan["configuration_hash"],
        "estimate_snapshot_hash": _exact_hash(
            estimate_snapshot_hash, "estimate_snapshot_hash",
        ),
        "execution_request_hash": _exact_hash(
            execution_request_hash, "execution_request_hash",
        ),
        **required,
        "budgets": budgets,
    }


class AgentApprovalStore:
    """Persist explicit local-CLI approval and consume it exactly once."""

    def __init__(
        self,
        root: str | Path,
        *,
        state_root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.root = _project_root(root)
        self.project_root_hash = _project_root_hash(self.root)
        self.state_root = _host_state_root(self.root, state_root, environ)
        project_digest = self.project_root_hash.removeprefix("sha256:")
        self.directory = (
            self.state_root
            / "universal-research-mcp"
            / "agent-approvals"
            / project_digest
        )

    def grant_path(self, approval_ref: str) -> Path:
        return self.directory / f"{_identifier(approval_ref, 'approval_ref')}.json"

    def consumed_path(self, approval_ref: str) -> Path:
        return self.directory / f"{_identifier(approval_ref, 'approval_ref')}.consumed.json"

    def create(
        self,
        run_plan: Mapping[str, Any],
        configuration: object,
        *,
        expected_run_plan_hash: str,
        expected_execution_request_hash: str,
        expires_at: str,
        estimate_snapshot_hash: str,
        execution_request_hash: str,
    ) -> dict[str, Any]:
        """Create a grant only when a repeated preflight matches user input."""

        binding = _exact_binding(
            run_plan,
            configuration,
            self.project_root_hash,
            _exact_hash(estimate_snapshot_hash, "estimate_snapshot_hash"),
            _exact_hash(execution_request_hash, "execution_request_hash"),
        )
        if binding["run_plan_hash"] != expected_run_plan_hash:
            raise AgentApprovalError("expected run plan hash does not match current preflight")
        if binding["execution_request_hash"] != expected_execution_request_hash:
            raise AgentApprovalError(
                "expected execution request hash does not match current preflight",
            )
        expiry, normalized_expiry = _utc_timestamp(expires_at, "expires_at")
        now = datetime.now(timezone.utc)
        if expiry <= now:
            raise AgentApprovalError("approval expiry must be in the future")
        grant = {
            "schema_version": GRANT_VERSION,
            **binding,
            "created_at": now.isoformat(),
            "expires_at": normalized_expiry,
            "authority_source": _AUTHORITY_SOURCE,
            "one_time": True,
        }
        grant["grant_hash"] = hash_without(grant, "grant_hash")
        try:
            self._create_json(self.grant_path(binding["approval_ref"]), grant)
        except FileExistsError as exc:
            raise AgentApprovalError("approval reference already exists and cannot be overwritten") from exc
        return self.summary(grant, consumed=False)

    def consume(
        self,
        run_plan: Mapping[str, Any],
        configuration: object,
        packets: tuple[dict[str, Any], ...],
        estimates: Mapping[str, Any],
        execution_request_hash: str,
    ) -> dict[str, Any]:
        """Validate exact bindings and atomically mark the grant consumed."""

        estimate_hash = artifact_hash(dict(estimates))
        binding = _exact_binding(
            run_plan,
            configuration,
            self.project_root_hash,
            estimate_hash,
            _exact_hash(execution_request_hash, "execution_request_hash"),
        )
        approval_ref = binding["approval_ref"]
        for packet in packets:
            authority = packet.get("authority") if isinstance(packet, dict) else None
            references = authority.get("approval_refs") if isinstance(authority, dict) else None
            if approval_ref not in set(references or []):
                raise AgentApprovalError("a task packet is not bound to the execution approval")
        grant = self._read_grant(approval_ref)
        for key, expected in binding.items():
            if grant.get(key) != expected:
                raise AgentApprovalError(f"approval grant does not match {key}")
        expiry, _normalized = _utc_timestamp(str(grant.get("expires_at") or ""), "expires_at")
        if expiry <= datetime.now(timezone.utc):
            raise AgentApprovalError("approval grant has expired")
        marker = {
            "schema_version": CONSUMPTION_VERSION,
            **binding,
            "grant_hash": grant["grant_hash"],
            "authority_source": grant["authority_source"],
            "consumed_at": datetime.now(timezone.utc).isoformat(),
            "consume_before_provider_call": True,
        }
        marker["consumption_hash"] = hash_without(marker, "consumption_hash")
        try:
            self._create_json(self.consumed_path(approval_ref), marker)
        except FileExistsError as exc:
            raise AgentApprovalError("approval grant was already consumed") from exc
        return {
            "approved": True,
            "project_root_hash": binding["project_root_hash"],
            "run_plan_hash": binding["run_plan_hash"],
            "estimate_snapshot_hash": binding["estimate_snapshot_hash"],
            "execution_request_hash": binding["execution_request_hash"],
            "provider_id": binding["provider_id"],
            "model": binding["model"],
            "provider_configuration_hash": binding["provider_configuration_hash"],
            "configuration_hash": binding["configuration_hash"],
            "approval_ref": approval_ref,
            "expires_at": grant["expires_at"],
            "grant_hash": grant["grant_hash"],
            "consumption_hash": marker["consumption_hash"],
            "authority_source": grant["authority_source"],
        }

    def _read_grant(self, approval_ref: str) -> dict[str, Any]:
        try:
            payload = self._read_bytes(self.grant_path(approval_ref))
            value = json.loads(payload.decode("utf-8"))
        except FileNotFoundError as exc:
            raise AgentApprovalError("no stored execution approval grant exists") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentApprovalError("execution approval grant is unreadable") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != GRANT_VERSION
            or set(value) != _GRANT_FIELDS
            or value.get("authority_source") != _AUTHORITY_SOURCE
            or value.get("one_time") is not True
        ):
            raise AgentApprovalError("execution approval grant schema is invalid")
        if value.get("grant_hash") != hash_without(value, "grant_hash"):
            raise AgentApprovalError("execution approval grant hash is invalid")
        created, _created_normalized = _utc_timestamp(
            str(value.get("created_at") or ""), "created_at",
        )
        expires, _expires_normalized = _utc_timestamp(
            str(value.get("expires_at") or ""), "expires_at",
        )
        if created >= expires:
            raise AgentApprovalError("execution approval grant timestamps are invalid")
        return value

    @staticmethod
    def summary(grant: Mapping[str, Any], *, consumed: bool) -> dict[str, Any]:
        return {
            "schema_version": GRANT_VERSION,
            "project_root_hash": grant.get("project_root_hash"),
            "approval_ref": grant.get("approval_ref"),
            "run_id": grant.get("run_id"),
            "run_plan_hash": grant.get("run_plan_hash"),
            "estimate_snapshot_hash": grant.get("estimate_snapshot_hash"),
            "execution_request_hash": grant.get("execution_request_hash"),
            "provider_id": grant.get("provider_id"),
            "model": grant.get("model"),
            "network_scope": grant.get("network_scope"),
            "provider_configuration_hash": grant.get("provider_configuration_hash"),
            "budgets": grant.get("budgets"),
            "created_at": grant.get("created_at"),
            "expires_at": grant.get("expires_at"),
            "grant_hash": grant.get("grant_hash"),
            "authority_source": grant.get("authority_source"),
            "consumed": consumed,
            "credential_values_exposed": False,
        }

    def _create_json(self, path: Path, value: Mapping[str, Any]) -> None:
        if path.parent != self.directory:
            raise AgentApprovalError("execution approval path escapes its directory")
        payload = (canonical_json(value) + "\n").encode("utf-8")
        if len(payload) > _MAX_APPROVAL_BYTES:
            raise AgentApprovalError("execution approval artifact is too large")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        directory_descriptor = self._open_directory()
        try:
            descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_descriptor)
            try:
                view = memoryview(payload)
                offset = 0
                while offset < len(view):
                    written = os.write(descriptor, view[offset:])
                    if written <= 0:
                        raise AgentApprovalError("partial execution approval write")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    def _read_bytes(self, path: Path) -> bytes:
        if path.parent != self.directory:
            raise AgentApprovalError("execution approval path escapes its directory")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        directory_descriptor = self._open_directory()
        try:
            descriptor = os.open(path.name, flags, dir_fd=directory_descriptor)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise AgentApprovalError("execution approval artifact is not a regular file")
                if metadata.st_size < 1 or metadata.st_size > _MAX_APPROVAL_BYTES:
                    raise AgentApprovalError("execution approval artifact size is invalid")
                chunks: list[bytes] = []
                received = 0
                while True:
                    chunk = os.read(descriptor, min(8192, _MAX_APPROVAL_BYTES + 1 - received))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                    if received > _MAX_APPROVAL_BYTES:
                        raise AgentApprovalError("execution approval artifact is too large")
                return b"".join(chunks)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_descriptor)

    def _open_directory(self) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.directory.anchor, flags)
        except OSError as exc:
            raise AgentApprovalError("host state filesystem root is unavailable or unsafe") from exc
        try:
            for component in self.directory.parts[1:]:
                created = False
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
                if created:
                    os.fsync(descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
                metadata = os.fstat(child)
                if not stat.S_ISDIR(metadata.st_mode):
                    os.close(child)
                    raise AgentApprovalError("execution approval path contains a non-directory")
                os.close(descriptor)
                descriptor = child
            return descriptor
        except AgentApprovalError:
            os.close(descriptor)
            raise
        except OSError as exc:
            os.close(descriptor)
            raise AgentApprovalError(
                "approval host state path contains an unsafe or inaccessible component",
            ) from exc


__all__ = ["AgentApprovalError", "AgentApprovalStore"]
