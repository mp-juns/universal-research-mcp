"""Explicit, project-local configuration for semantic candidate retrieval.

This configuration intentionally permits only offline backends. It is read by
the MCP server to encode a query, but never causes a model download or a remote
request. Remote embedding remains a separately governed future capability.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Literal


SCHEMA_VERSION = "semantic-retrieval-config/1.0"
CONFIG_RELATIVE_PATH = Path("config/semantic.json")
BackendKind = Literal["signed_hashing_v1", "local_sentence_transformer"]


def configuration_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / CONFIG_RELATIVE_PATH


def _validate(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict) or set(config) != {
        "schema_version", "backend", "auto_refresh",
    }:
        raise ValueError("semantic configuration is incomplete")
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("semantic configuration schema_version is invalid")
    if not isinstance(config.get("auto_refresh"), bool):
        raise ValueError("semantic configuration auto_refresh must be boolean")
    backend = config.get("backend")
    if not isinstance(backend, dict) or not isinstance(backend.get("kind"), str):
        raise ValueError("semantic configuration backend is invalid")
    kind = backend["kind"]
    if kind == "signed_hashing_v1":
        if set(backend) != {"kind", "dimensions"}:
            raise ValueError("signed hashing configuration is invalid")
        dimensions = backend.get("dimensions")
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or not 8 <= dimensions <= 4096:
            raise ValueError("signed hashing dimensions must be in [8, 4096]")
    elif kind == "local_sentence_transformer":
        expected = {"kind", "model_path", "device", "trust_local_model_code", "dimensions"}
        if set(backend) != expected:
            raise ValueError("local semantic configuration is invalid")
        if not isinstance(backend.get("model_path"), str) or not backend["model_path"]:
            raise ValueError("local semantic model_path is required")
        if backend.get("device") not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError("local semantic device is invalid")
        if not isinstance(backend.get("trust_local_model_code"), bool):
            raise ValueError("local semantic trust_local_model_code must be boolean")
        dimensions = backend.get("dimensions")
        if dimensions is not None and (
            not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 1
        ):
            raise ValueError("local semantic dimensions must be positive or null")
    else:
        raise ValueError("semantic backend is not supported")
    return config


def load_semantic_config(root: str | Path) -> dict[str, Any] | None:
    path = configuration_path(root)
    if not path.is_file():
        return None
    return _validate(json.loads(path.read_text(encoding="utf-8")))


def write_semantic_config(root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    normalized = _validate(config)
    path = configuration_path(root)
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
    return normalized


def configure_demo(root: str | Path, *, dimensions: int = 256, auto_refresh: bool = False) -> dict[str, Any]:
    config = write_semantic_config(root, {
        "schema_version": SCHEMA_VERSION,
        "backend": {"kind": "signed_hashing_v1", "dimensions": dimensions},
        "auto_refresh": auto_refresh,
    })
    return {
        "configured": True,
        "configuration_path": str(configuration_path(root)),
        "backend": config["backend"],
        "backend_class": "deterministic_demo",
        "trained_embedding_model": False,
        "auto_refresh": auto_refresh,
    }


def configure_local(
    root: str | Path,
    *,
    model_path: str | Path,
    device: str = "auto",
    trust_local_model_code: bool = False,
    dimensions: int | None = None,
    auto_refresh: bool = False,
) -> dict[str, Any]:
    resolved = Path(model_path).expanduser().resolve()
    config = write_semantic_config(root, {
        "schema_version": SCHEMA_VERSION,
        "backend": {
            "kind": "local_sentence_transformer",
            "model_path": str(resolved),
            "device": device,
            "trust_local_model_code": trust_local_model_code,
            "dimensions": dimensions,
        },
        "auto_refresh": auto_refresh,
    })
    return {
        "configured": True,
        "configuration_path": str(configuration_path(root)),
        "backend": {
            "kind": config["backend"]["kind"],
            "model_path": str(resolved),
            "device": device,
            "dimensions": dimensions,
        },
        "backend_class": "local_trained_model",
        "trained_embedding_model": True,
        "auto_refresh": auto_refresh,
        "downloads_performed": False,
    }


__all__ = [
    "BackendKind", "CONFIG_RELATIVE_PATH", "SCHEMA_VERSION", "configuration_path",
    "configure_demo", "configure_local", "load_semantic_config", "write_semantic_config",
]
