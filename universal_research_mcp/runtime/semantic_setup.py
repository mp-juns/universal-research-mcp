"""Explicit local semantic-environment planning and execution.

This module is intentionally outside the MCP query path.  It never runs from
``pip install`` and it never contacts a model registry until a caller repeats a
hash of the displayed setup plan with ``--execute``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Literal

from universal_research_mcp import __version__
from universal_research_mcp.runtime.semantic_config import configure_local


SETUP_SCHEMA_VERSION = "semantic-local-setup/1.0"
EnvironmentManager = Literal["auto", "conda", "venv"]


@dataclass(frozen=True)
class RecommendedModel:
    model_id: str
    model_card: str
    languages: str
    dimensions: int
    size_class: str
    purpose: str
    query_prefix: str | None
    recommended: bool = False


# This is a deliberately finite allowlist.  Adding a model is a reviewed
# package change; arbitrary repository IDs cannot become executable downloads
# through a chat request or project file.
RECOMMENDED_MODELS = (
    RecommendedModel("sentence-transformers/all-MiniLM-L6-v2", "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2", "English", 384, "small", "fast English baseline", None),
    RecommendedModel("sentence-transformers/all-mpnet-base-v2", "https://huggingface.co/sentence-transformers/all-mpnet-base-v2", "English", 768, "medium", "higher-quality English retrieval", None),
    RecommendedModel("BAAI/bge-small-en-v1.5", "https://huggingface.co/BAAI/bge-small-en-v1.5", "English", 384, "small", "compact English retrieval", None),
    RecommendedModel("BAAI/bge-base-en-v1.5", "https://huggingface.co/BAAI/bge-base-en-v1.5", "English", 768, "medium", "balanced English retrieval", None),
    RecommendedModel("BAAI/bge-large-en-v1.5", "https://huggingface.co/BAAI/bge-large-en-v1.5", "English", 1024, "large", "quality-oriented English retrieval", None),
    RecommendedModel("intfloat/e5-small-v2", "https://huggingface.co/intfloat/e5-small-v2", "English", 384, "small", "compact query/passage retrieval", "query: "),
    RecommendedModel("intfloat/e5-base-v2", "https://huggingface.co/intfloat/e5-base-v2", "English", 768, "medium", "balanced query/passage retrieval", "query: "),
    RecommendedModel("intfloat/multilingual-e5-small", "https://huggingface.co/intfloat/multilingual-e5-small", "multilingual", 384, "small", "compact multilingual retrieval", "query: "),
    RecommendedModel("intfloat/multilingual-e5-base", "https://huggingface.co/intfloat/multilingual-e5-base", "multilingual", 768, "medium", "Korean and multilingual research retrieval", "query: ", True),
    RecommendedModel("BAAI/bge-m3", "https://huggingface.co/BAAI/bge-m3", "multilingual", 1024, "large", "quality-oriented multilingual and long-document retrieval", None),
)
_MODELS_BY_ID = {model.model_id: model for model in RECOMMENDED_MODELS}


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def catalogue() -> dict[str, Any]:
    """Return model facts only; no registry request occurs."""

    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "status": "catalogue",
        "models": [
            {
                "model_id": model.model_id,
                "model_card": model.model_card,
                "languages": model.languages,
                "dimensions": model.dimensions,
                "size_class": model.size_class,
                "purpose": model.purpose,
                "query_prefix": model.query_prefix,
                "recommended": model.recommended,
                "downloaded": False,
            }
            for model in RECOMMENDED_MODELS
        ],
        "model_count": len(RECOMMENDED_MODELS),
        "network_used": False,
    }


def _select_manager(manager: EnvironmentManager) -> tuple[str, str | None]:
    conda = shutil.which("conda")
    if manager == "auto":
        return ("conda", conda) if conda else ("venv", None)
    if manager == "conda" and not conda:
        raise ValueError("Conda was requested but was not found on PATH")
    return manager, conda if manager == "conda" else None


def _environment_python(path: Path) -> Path:
    return path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _environment_cli(path: Path) -> Path:
    return path / ("Scripts/universal-research.exe" if os.name == "nt" else "bin/universal-research")


def _safe_model_folder(model_id: str) -> str:
    return model_id.replace("/", "--")


def setup_plan(
    root: str | Path,
    *,
    model_id: str,
    manager: EnvironmentManager = "auto",
    device: str = "auto",
    revision: str = "main",
    auto_refresh: bool = False,
    reuse_existing: bool = False,
) -> dict[str, Any]:
    """Create a non-mutating, hash-bound local setup plan."""

    model = _MODELS_BY_ID.get(model_id)
    if model is None:
        raise ValueError("model_id is not in the reviewed semantic model catalogue")
    if device not in {"auto", "cpu", "cuda", "mps"}:
        raise ValueError("semantic setup device is invalid")
    if not isinstance(revision, str) or not revision or len(revision) > 128:
        raise ValueError("semantic setup revision is invalid")
    selected_manager, conda = _select_manager(manager)
    project_root = Path(root).expanduser().resolve()
    environment_path = project_root / ".universal-research" / "semantic-env"
    model_path = project_root / ".universal-research" / "models" / _safe_model_folder(model.model_id)
    environment_exists = environment_path.exists()
    model_exists = model_path.exists()
    if environment_exists and not reuse_existing:
        environment_state = "exists_reuse_requires_explicit_flag"
    else:
        environment_state = "existing" if environment_exists else "will_create"
    if model_exists and not reuse_existing:
        model_state = "exists_reuse_requires_explicit_flag"
    else:
        model_state = "existing" if model_exists else "will_download"
    plan = {
        "schema_version": SETUP_SCHEMA_VERSION,
        "status": "confirmation_required",
        "root": str(project_root),
        "environment": {
            "manager": selected_manager,
            "conda_path": conda,
            "path": str(environment_path),
            "state": environment_state,
            "python": str(_environment_python(environment_path)),
        },
        "model": {
            "model_id": model.model_id,
            "requested_revision": revision,
            "path": str(model_path),
            "state": model_state,
            "dimensions": model.dimensions,
            "languages": model.languages,
            "size_class": model.size_class,
            "query_prefix": model.query_prefix,
        },
        "semantic_configuration": {
            "device": device,
            "auto_refresh": auto_refresh,
            "trust_local_model_code": False,
        },
        "package": {
            "requirement": f"universal-research-mcp[semantic]=={__version__}",
            "install_semantic_extra": True,
        },
        "operations": [
            "create_or_reuse_isolated_environment",
            "install_pinned_universal_research_semantic_extra",
            "download_or_reuse_reviewed_model_snapshot",
            "write_project_local_semantic_configuration",
        ],
        # Even a reused environment may need pip to resolve the package extra,
        # so plan conservatively and require an approved network-capable step.
        "network": {"required_on_execute": True},
        "model_execution": False,
        "index_build": False,
        "reuse_existing": reuse_existing,
    }
    plan["plan_sha256"] = _sha256(plan)
    return plan


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _download_model(python: Path, *, model_id: str, revision: str, destination: Path) -> None:
    script = (
        "from huggingface_hub import snapshot_download; import sys; "
        "snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3])"
    )
    _run([str(python), "-c", script, model_id, revision, str(destination)])


def execute_setup(plan: dict[str, Any], *, confirm_plan_sha256: str) -> dict[str, Any]:
    """Perform only the exact planned environment and model operations once confirmed."""

    supplied = dict(plan)
    claimed = supplied.pop("plan_sha256", None)
    actual = _sha256(supplied)
    if claimed != actual or confirm_plan_sha256 != actual:
        raise ValueError("--confirm-plan-sha256 must exactly match the displayed setup plan")
    if plan["status"] != "confirmation_required":
        raise ValueError("semantic setup plan has an invalid status")
    environment = plan["environment"]
    model = plan["model"]
    env_path = Path(environment["path"])
    model_path = Path(model["path"])
    reuse = bool(plan["reuse_existing"])
    if environment["state"] == "exists_reuse_requires_explicit_flag":
        raise ValueError("semantic environment exists; regenerate the plan with --reuse-existing")
    if model["state"] == "exists_reuse_requires_explicit_flag":
        raise ValueError("semantic model path exists; regenerate the plan with --reuse-existing")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        if environment["manager"] == "conda":
            conda = environment["conda_path"]
            if not isinstance(conda, str) or not conda:
                raise ValueError("semantic setup plan has no usable Conda path")
            _run([conda, "create", "--yes", "--prefix", str(env_path), "python=3.11"])
        elif environment["manager"] == "venv":
            _run([sys.executable, "-m", "venv", str(env_path)])
        else:
            raise ValueError("semantic setup environment manager is invalid")
    python = _environment_python(env_path)
    if not python.is_file():
        raise RuntimeError("semantic environment was created without its expected Python executable")
    _run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    _run([str(python), "-m", "pip", "install", "--upgrade", plan["package"]["requirement"]])

    if not model_path.exists():
        model_path.parent.mkdir(parents=True, exist_ok=True)
        _download_model(
            python,
            model_id=model["model_id"],
            revision=model["requested_revision"],
            destination=model_path,
        )
    if not model_path.is_dir():
        raise RuntimeError("semantic model download did not create the expected local directory")
    configure_local(
        plan["root"],
        model_path=model_path,
        device=plan["semantic_configuration"]["device"],
        trust_local_model_code=False,
        dimensions=model["dimensions"],
        auto_refresh=plan["semantic_configuration"]["auto_refresh"],
    )
    environment_cli = _environment_cli(env_path)
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "status": "configured",
        "plan_sha256": actual,
        "environment": {"manager": environment["manager"], "path": str(env_path)},
        "model": {"model_id": model["model_id"], "requested_revision": model["requested_revision"], "path": str(model_path)},
        "semantic_configuration_written": True,
        "index_build_executed": False,
        "next_steps": {
            "build_index": [str(environment_cli), "semantic", "build", "--root", plan["root"]],
            "serve": [str(environment_cli), "serve", "--root", plan["root"], "--auto-index"],
        },
        "network_used": True,
        "model_execution": False,
        "reuse_existing": reuse,
    }


__all__ = [
    "EnvironmentManager", "RECOMMENDED_MODELS", "SETUP_SCHEMA_VERSION", "catalogue", "execute_setup", "setup_plan",
]
