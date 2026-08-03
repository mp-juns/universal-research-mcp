"""Secret-free provider configuration stored inside one research project."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from universal_research_mcp.providers import (
    LOOPBACK_PROVIDER_ID,
    Capability,
    CredentialRef,
    validate_loopback_endpoint,
)
from universal_research_mcp.runtime.paths import ProjectPaths


CONFIG_VERSION = "provider-config/2.0"
LEGACY_CONFIG_VERSIONS = frozenset({"provider-config/1.0"})
CONFIG_RELATIVE_PATH = "config/providers.json"
REMOTE_CAPABILITIES = {
    "openai": frozenset({"embedding", "generation"}),
    "anthropic": frozenset({"generation"}),
}


def _path(root: str | Path) -> Path:
    return ProjectPaths.from_root(root).resolve_relative(CONFIG_RELATIVE_PATH)


def _empty() -> dict[str, Any]:
    return {"schema_version": CONFIG_VERSION, "embedding": {}, "generation": {}}


def _validate(config: Mapping[str, Any]) -> dict[str, Any]:
    if config.get("schema_version") not in {CONFIG_VERSION, *LEGACY_CONFIG_VERSIONS}:
        raise ValueError("unsupported provider configuration version")
    if set(config) != {"schema_version", "embedding", "generation"}:
        raise ValueError("provider configuration contains unknown fields")
    normalized = _empty()
    for capability in ("embedding", "generation"):
        value = config.get(capability)
        if not isinstance(value, Mapping):
            raise ValueError(f"provider configuration {capability} must be an object")
        if set(value) - {"local", "loopback", "remote"}:
            raise ValueError(f"provider configuration {capability} contains unknown fields")
        if "local" in value:
            if capability != "embedding" or not isinstance(value["local"], Mapping):
                raise ValueError("only local embedding configuration is supported")
            local = value["local"]
            if set(local) != {"model_path", "device", "trust_local_model_code"}:
                raise ValueError("local embedding configuration is incomplete")
            if not isinstance(local.get("model_path"), str) or not local["model_path"]:
                raise ValueError("local embedding model_path is required")
            if local.get("device") not in {"auto", "cpu", "cuda", "mps"}:
                raise ValueError("local embedding device is invalid")
            if not isinstance(local.get("trust_local_model_code"), bool):
                raise ValueError("trust_local_model_code must be boolean")
            normalized[capability]["local"] = dict(local)
        if "loopback" in value:
            if capability != "generation" or not isinstance(value["loopback"], Mapping):
                raise ValueError("only loopback generation configuration is supported")
            loopback = value["loopback"]
            if set(loopback) != {"provider_id", "endpoint", "model", "credential_ref"}:
                raise ValueError("loopback generation configuration is incomplete")
            if loopback.get("provider_id") != LOOPBACK_PROVIDER_ID:
                raise ValueError("loopback provider_id is invalid")
            endpoint = validate_loopback_endpoint(loopback.get("endpoint"))
            if not isinstance(loopback.get("model"), str) or not loopback["model"]:
                raise ValueError("loopback provider model is required")
            raw_reference = loopback.get("credential_ref")
            credential_ref = None
            if raw_reference is not None:
                if not isinstance(raw_reference, str):
                    raise ValueError("loopback credential_ref must be a string or null")
                credential_ref = str(CredentialRef.parse(raw_reference))
            normalized[capability]["loopback"] = {
                "provider_id": LOOPBACK_PROVIDER_ID,
                "endpoint": endpoint,
                "model": loopback["model"],
                "credential_ref": credential_ref,
            }
        if "remote" in value:
            remote = value["remote"]
            if not isinstance(remote, Mapping) or set(remote) != {
                "provider_id", "model", "credential_ref",
            }:
                raise ValueError(f"remote {capability} configuration is incomplete")
            provider_id = remote.get("provider_id")
            if provider_id not in REMOTE_CAPABILITIES or capability not in REMOTE_CAPABILITIES[provider_id]:
                raise ValueError(f"{provider_id} does not provide approved {capability} capability")
            if not isinstance(remote.get("model"), str) or not remote["model"]:
                raise ValueError("remote provider model is required")
            credential = CredentialRef.parse(str(remote.get("credential_ref") or ""))
            normalized[capability]["remote"] = {
                "provider_id": provider_id,
                "model": remote["model"],
                "credential_ref": str(credential),
            }
    return normalized


def load_provider_config(root: str | Path) -> dict[str, Any]:
    path = _path(root)
    if not path.is_file():
        return _empty()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ValueError("provider configuration root must be an object")
    return _validate(loaded)


def _write(root: str | Path, config: Mapping[str, Any]) -> Path:
    normalized = _validate(config)
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".staging", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def configure_remote_provider(
    root: str | Path,
    *,
    capability: Capability | str,
    provider_id: str,
    model: str,
    credential_ref: str,
) -> dict[str, Any]:
    capability_name = capability.value if isinstance(capability, Capability) else str(capability)
    if capability_name not in {"embedding", "generation"}:
        raise ValueError("provider capability is invalid")
    config = load_provider_config(root)
    config[capability_name]["remote"] = {
        "provider_id": provider_id,
        "model": model,
        "credential_ref": credential_ref,
    }
    path = _write(root, config)
    return {"configured": True, "capability": capability_name, "provider_id": provider_id, "path": str(path)}


def configure_local_embedding(
    root: str | Path,
    *,
    model_path: str | Path,
    device: str = "auto",
    trust_local_model_code: bool = False,
) -> dict[str, Any]:
    config = load_provider_config(root)
    config["embedding"]["local"] = {
        "model_path": str(Path(model_path).expanduser().resolve()),
        "device": device,
        "trust_local_model_code": trust_local_model_code,
    }
    path = _write(root, config)
    return {"configured": True, "capability": "embedding", "provider_id": "local", "path": str(path)}


def configure_loopback_generation(
    root: str | Path,
    *,
    endpoint: str,
    model: str,
    credential_ref: str | None = None,
) -> dict[str, Any]:
    """Configure, but never contact or authorize, one loopback model server."""

    config = load_provider_config(root)
    config["generation"]["loopback"] = {
        "provider_id": LOOPBACK_PROVIDER_ID,
        "endpoint": endpoint,
        "model": model,
        "credential_ref": credential_ref,
    }
    path = _write(root, config)
    return {
        "configured": True,
        "capability": "generation",
        "provider_id": LOOPBACK_PROVIDER_ID,
        "path": str(path),
    }


def provider_configuration_status(root: str | Path) -> dict[str, Any]:
    config = load_provider_config(root)
    result: dict[str, Any] = {
        "selection_policy": "local_first_then_explicit_network_provider_opt_in",
        "configuration_path": str(_path(root)),
        "embedding": {},
        "generation": {},
        "secret_values_exposed": False,
    }
    for capability in ("embedding", "generation"):
        configured = config[capability]
        if "local" in configured:
            local = configured["local"]
            result[capability]["local"] = {
                "configured": True,
                "snapshot_exists": Path(local["model_path"]).is_file()
                or Path(local["model_path"]).is_dir(),
                "device": local["device"],
                "trust_local_model_code": local["trust_local_model_code"],
            }
        if "remote" in configured:
            remote = configured["remote"]
            reference = CredentialRef.parse(remote["credential_ref"])
            credential_present = (
                bool(os.environ.get(reference.locator)) if reference.kind == "env" else None
            )
            result[capability]["remote"] = {
                "configured": True,
                "provider_id": remote["provider_id"],
                "model": remote["model"],
                "credential_kind": reference.kind,
                "credential_available": credential_present,
                "requires_per_run_approval_and_budget": True,
            }
        if "loopback" in configured:
            loopback = configured["loopback"]
            reference = (
                None
                if loopback["credential_ref"] is None
                else CredentialRef.parse(loopback["credential_ref"])
            )
            credential_present = None
            if reference is not None and reference.kind == "env":
                credential_present = bool(os.environ.get(reference.locator))
            result[capability]["loopback"] = {
                "configured": True,
                "provider_id": loopback["provider_id"],
                "model": loopback["model"],
                "endpoint": loopback["endpoint"],
                "network_scope": "loopback",
                "credential_configured": reference is not None,
                "credential_kind": None if reference is None else reference.kind,
                "credential_available": credential_present,
                "requires_per_run_approval_and_budget": True,
            }
    return result


__all__ = [
    "configure_local_embedding",
    "configure_loopback_generation",
    "configure_remote_provider",
    "load_provider_config",
    "provider_configuration_status",
]
