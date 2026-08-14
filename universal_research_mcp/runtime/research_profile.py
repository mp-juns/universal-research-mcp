"""Strict, project-local research routing policy.

The profile is deliberately declarative.  It can select a supported offline
retrieval backend and record which future provider routes are permitted, but it
cannot download models, read credentials, contact providers, create Skills, or
launch Codex subagents.  Those operations remain separately host-governed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


SCHEMA_VERSION = "research-profile/1.0"
CONFIG_RELATIVE_PATH = Path("config/research-profile.json")
REGISTERED_SKILL_IDS = frozenset({"research-governance", "research-workflow"})
_CREDENTIAL_REF = re.compile(r"env:[A-Z_][A-Z0-9_]*\Z")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


def configuration_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / CONFIG_RELATIVE_PATH


def profile_template() -> dict[str, Any]:
    """Return the safe default; it does not activate semantic retrieval."""

    return {
        "schema_version": SCHEMA_VERSION,
        "retrieval": {
            "mode": "lexical",
            "semantic_backend": {"kind": "disabled"},
        },
        "execution": {
            "host": "codex",
            "subagent_mode": "disabled",
            "max_parallel_agents": 1,
            "registered_skill_ids": ["research-governance", "research-workflow"],
        },
        "provider_policy": {
            "network_enabled": False,
            "allowed_routes": [{"kind": "none"}],
        },
        "source_scope": {
            "include_kinds": ["build_definition", "configuration", "documentation", "source_code"],
            "exclude_generated": True,
            "exclude_secrets": True,
            "max_candidate_files": 200,
            "max_file_bytes": 1_048_576,
        },
    }


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def profile_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _require_keys(value: Any, expected: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"research profile {name} is invalid")
    return value


def _positive_int(value: Any, name: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"research profile {name} must be in [1, {maximum}]")
    return value


def _validate_semantic_backend(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("research profile semantic_backend is invalid")
    backend = value
    kind = backend.get("kind")
    if kind == "disabled":
        _require_keys(backend, {"kind"}, "disabled semantic_backend")
    elif kind == "demo":
        _require_keys(backend, {"kind", "dimensions", "auto_refresh"}, "demo semantic_backend")
        _positive_int(backend["dimensions"], "semantic_backend.dimensions", 4096)
        if backend["dimensions"] < 8:
            raise ValueError("research profile demo dimensions must be in [8, 4096]")
        if not isinstance(backend["auto_refresh"], bool):
            raise ValueError("research profile demo auto_refresh must be boolean")
    elif kind == "local":
        _require_keys(
            backend,
            {"kind", "model_path", "device", "trust_local_model_code", "dimensions", "auto_refresh"},
            "local semantic_backend",
        )
        if not isinstance(backend["model_path"], str) or not backend["model_path"]:
            raise ValueError("research profile local model_path is required")
        if backend["device"] not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError("research profile local device is invalid")
        if not isinstance(backend["trust_local_model_code"], bool):
            raise ValueError("research profile local trust_local_model_code must be boolean")
        dimensions = backend["dimensions"]
        if dimensions is not None:
            _positive_int(dimensions, "semantic_backend.dimensions", 1_000_000)
        if not isinstance(backend["auto_refresh"], bool):
            raise ValueError("research profile local auto_refresh must be boolean")
    else:
        raise ValueError("research profile semantic_backend kind is unsupported")
    return backend


def _validate_routes(value: Any, network_enabled: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("research profile allowed_routes must be a non-empty list")
    routes: list[dict[str, Any]] = []
    for route in value:
        if not isinstance(route, dict) or not isinstance(route.get("kind"), str):
            raise ValueError("research profile provider route is invalid")
        kind = route["kind"]
        if kind == "none":
            _require_keys(route, {"kind"}, "none provider route")
        elif kind == "loopback":
            _require_keys(route, {"kind", "model"}, "loopback provider route")
            if not isinstance(route["model"], str) or not _MODEL_ID.fullmatch(route["model"]):
                raise ValueError("research profile loopback model is invalid")
        elif kind in {"openai", "anthropic"}:
            _require_keys(route, {"kind", "model", "credential_ref"}, "remote provider route")
            if not network_enabled:
                raise ValueError("research profile remote provider route requires network_enabled")
            if not isinstance(route["model"], str) or not _MODEL_ID.fullmatch(route["model"]):
                raise ValueError("research profile remote model is invalid")
            if not isinstance(route["credential_ref"], str) or not _CREDENTIAL_REF.fullmatch(route["credential_ref"]):
                raise ValueError("research profile credential_ref must be an environment-variable reference")
        else:
            raise ValueError("research profile provider route kind is unsupported")
        routes.append(route)
    if len({canonical_json(route) for route in routes}) != len(routes):
        raise ValueError("research profile provider routes must not repeat")
    return routes


def validate_profile(value: Any) -> dict[str, Any]:
    profile = _require_keys(
        value,
        {"schema_version", "retrieval", "execution", "provider_policy", "source_scope"},
        "document",
    )
    if profile["schema_version"] != SCHEMA_VERSION:
        raise ValueError("research profile schema_version is invalid")

    retrieval = _require_keys(profile["retrieval"], {"mode", "semantic_backend"}, "retrieval")
    if retrieval["mode"] not in {"lexical", "semantic", "hybrid"}:
        raise ValueError("research profile retrieval mode is invalid")
    semantic_backend = _validate_semantic_backend(retrieval["semantic_backend"])
    if retrieval["mode"] == "lexical" and semantic_backend["kind"] != "disabled":
        raise ValueError("research profile lexical mode requires a disabled semantic backend")
    if retrieval["mode"] in {"semantic", "hybrid"} and semantic_backend["kind"] == "disabled":
        raise ValueError("research profile semantic or hybrid mode requires a configured semantic backend")

    execution = _require_keys(
        profile["execution"], {"host", "subagent_mode", "max_parallel_agents", "registered_skill_ids"}, "execution",
    )
    if execution["host"] != "codex":
        raise ValueError("research profile only supports the codex host")
    if execution["subagent_mode"] not in {"disabled", "codex_native"}:
        raise ValueError("research profile subagent_mode is invalid")
    _positive_int(execution["max_parallel_agents"], "execution.max_parallel_agents", 8)
    if execution["subagent_mode"] == "disabled" and execution["max_parallel_agents"] != 1:
        raise ValueError("research profile disabled subagents require max_parallel_agents=1")
    skill_ids = execution["registered_skill_ids"]
    if not isinstance(skill_ids, list) or skill_ids != sorted(set(skill_ids)) or not all(
        isinstance(skill_id, str) and skill_id in REGISTERED_SKILL_IDS for skill_id in skill_ids
    ):
        raise ValueError("research profile registered_skill_ids must be sorted registered skill IDs")

    provider_policy = _require_keys(profile["provider_policy"], {"network_enabled", "allowed_routes"}, "provider_policy")
    if not isinstance(provider_policy["network_enabled"], bool):
        raise ValueError("research profile network_enabled must be boolean")
    _validate_routes(provider_policy["allowed_routes"], provider_policy["network_enabled"])

    source_scope = _require_keys(
        profile["source_scope"],
        {"include_kinds", "exclude_generated", "exclude_secrets", "max_candidate_files", "max_file_bytes"},
        "source_scope",
    )
    allowed_kinds = {"documentation", "source_code", "build_definition", "configuration"}
    include_kinds = source_scope["include_kinds"]
    if not isinstance(include_kinds, list) or include_kinds != sorted(set(include_kinds)) or not include_kinds or not all(
        isinstance(kind, str) and kind in allowed_kinds for kind in include_kinds
    ):
        raise ValueError("research profile source_scope include_kinds is invalid")
    if not isinstance(source_scope["exclude_generated"], bool) or not isinstance(source_scope["exclude_secrets"], bool):
        raise ValueError("research profile source_scope exclusions must be boolean")
    if not source_scope["exclude_secrets"]:
        raise ValueError("research profile must exclude secrets")
    _positive_int(source_scope["max_candidate_files"], "source_scope.max_candidate_files", 10_000)
    _positive_int(source_scope["max_file_bytes"], "source_scope.max_file_bytes", 16 * 1024 * 1024)
    return profile


def load_profile(root: str | Path) -> dict[str, Any] | None:
    path = configuration_path(root)
    if not path.is_file():
        return None
    return validate_profile(json.loads(path.read_text(encoding="utf-8")))


def write_profile(root: str | Path, value: dict[str, Any]) -> dict[str, Any]:
    profile = validate_profile(value)
    path = configuration_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".staging", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(profile, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return profile


def semantic_config_from_profile(root: str | Path) -> dict[str, Any] | None:
    """Translate a configured profile into the existing offline semantic contract."""

    profile = load_profile(root)
    if profile is None:
        return None
    backend = profile["retrieval"]["semantic_backend"]
    if backend["kind"] == "disabled":
        return None
    if backend["kind"] == "demo":
        return {
            "schema_version": "semantic-retrieval-config/1.0",
            "backend": {"kind": "signed_hashing_v1", "dimensions": backend["dimensions"]},
            "auto_refresh": backend["auto_refresh"],
        }
    return {
        "schema_version": "semantic-retrieval-config/1.0",
        "backend": {
            "kind": "local_sentence_transformer",
            "model_path": str(Path(backend["model_path"]).expanduser().resolve()),
            "device": backend["device"],
            "trust_local_model_code": backend["trust_local_model_code"],
            "dimensions": backend["dimensions"],
        },
        "auto_refresh": backend["auto_refresh"],
    }


def profile_status(root: str | Path) -> dict[str, Any]:
    profile = load_profile(root)
    if profile is None:
        return {
            "status": "missing",
            "configuration_path": str(configuration_path(root)),
            "execution": "not_configured",
            "provider_execution": "not_supported_by_public_mcp",
        }
    return {
        "status": "configured",
        "configuration_path": str(configuration_path(root)),
        "profile_sha256": profile_sha256(profile),
        "profile": profile,
        "semantic_backend_derived": semantic_config_from_profile(root) is not None,
        "execution": "declarative_only",
        "provider_execution": "not_supported_by_public_mcp",
        "subagent_execution": "host_governed_only",
    }


__all__ = [
    "CONFIG_RELATIVE_PATH", "REGISTERED_SKILL_IDS", "SCHEMA_VERSION", "configuration_path",
    "load_profile", "profile_sha256", "profile_status", "profile_template", "semantic_config_from_profile",
    "validate_profile", "write_profile",
]
