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
import tempfile
from typing import Any, Literal

from universal_research_mcp import __version__
from universal_research_mcp.runtime.model_snapshot import (
    SnapshotIdentity, create_snapshot_manifest, immutable_revision,
    read_snapshot_identity, verify_snapshot,
)
from universal_research_mcp.runtime.project_io import ProjectFiles
from universal_research_mcp.runtime.semantic_config import configure_local


SETUP_SCHEMA_VERSION = "semantic-local-setup/2.0"
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
    if manager not in {"auto", "conda", "venv"}:
        raise ValueError("semantic setup environment manager is invalid")
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
    revision: str,
    auto_refresh: bool = False,
    reuse_existing: bool = False,
) -> dict[str, Any]:
    """Create a non-mutating, hash-bound local setup plan."""

    model = _MODELS_BY_ID.get(model_id)
    if model is None:
        raise ValueError("model_id is not in the reviewed semantic model catalogue")
    if device not in {"auto", "cpu", "cuda", "mps"}:
        raise ValueError("semantic setup device is invalid")
    revision = immutable_revision(revision)
    if not isinstance(auto_refresh, bool) or not isinstance(reuse_existing, bool):
        raise ValueError("semantic setup flags must be boolean")
    selected_manager, conda = _select_manager(manager)
    project_root = Path(root).expanduser().resolve()
    environment_path = project_root / ".universal-research" / "semantic-env"
    model_path = project_root / ".universal-research" / "models" / _safe_model_folder(model.model_id) / revision
    files = ProjectFiles(project_root)
    files.path(environment_path, directory=True)
    files.path(model_path, directory=True)
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
    snapshot = None
    if model_exists and reuse_existing:
        snapshot = read_snapshot_identity(model_path)
        if snapshot.model_id != model_id or snapshot.revision != revision:
            raise ValueError("model snapshot identity does not match the requested model and revision")
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
            "resolved_revision": revision,
            "path": str(model_path),
            "state": model_state,
            "dimensions": model.dimensions,
            "languages": model.languages,
            "size_class": model.size_class,
            "query_prefix": model.query_prefix,
            "snapshot": snapshot.to_dict() if snapshot is not None else None,
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
            "download_pinned_snapshot_or_verify_existing_manifest_and_files",
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
    if plan.get("schema_version") != SETUP_SCHEMA_VERSION:
        raise ValueError("semantic setup plan schema changed; regenerate and approve a new plan")
    if plan.get("status") != "confirmation_required":
        raise ValueError("semantic setup plan has an invalid status")
    _check_current_plan(plan)
    root = Path(plan["root"])
    root.mkdir(parents=True, exist_ok=True)
    files = ProjectFiles(root)
    with files.parent(".universal-research/.semantic-setup.lock", create=True) as (parent, name):
        try:
            identity = parent.create(name, _canonical_json({"plan_sha256": actual, "pid": os.getpid()}))
        except FileExistsError as exc:
            raise RuntimeError("semantic setup lock exists; another setup may be active") from exc
        try:
            _check_current_plan(plan)
            return _execute_confirmed(plan, actual)
        finally:
            parent.remove(name, identity=identity)


def _check_current_plan(plan: dict[str, Any]) -> None:
    """A confirmation hash is not permission to change the planner's contract."""

    try:
        current = setup_plan(
            plan["root"], model_id=plan["model"]["model_id"],
            manager=plan["environment"]["manager"],
            device=plan["semantic_configuration"]["device"],
            revision=plan["model"]["requested_revision"],
            auto_refresh=plan["semantic_configuration"]["auto_refresh"],
            reuse_existing=plan["reuse_existing"],
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("semantic setup plan is incomplete; regenerate the plan") from exc
    if current != plan:
        raise ValueError("semantic setup plan or local state changed; regenerate and approve the plan")
    for target in ("environment", "model"):
        if plan[target]["state"] == "exists_reuse_requires_explicit_flag":
            raise ValueError(f"semantic {target} exists; regenerate the plan with --reuse-existing")


def _execute_confirmed(plan: dict[str, Any], actual: str) -> dict[str, Any]:
    environment = plan["environment"]
    model = plan["model"]
    env_path = Path(environment["path"])
    model_path = Path(model["path"])
    reuse = bool(plan["reuse_existing"])
    snapshot = SnapshotIdentity.from_dict(model["snapshot"]) if model["snapshot"] is not None else None
    if snapshot is not None:
        # Check before any environment creation, package install or download.
        verify_snapshot(model_path, snapshot)

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

    files = ProjectFiles(Path(plan["root"]))
    files.path(model_path, directory=True)
    if snapshot is None:
        if model_path.exists():
            raise ValueError("semantic model path appeared after approval; regenerate the plan")
        model_path.parent.mkdir(parents=True, exist_ok=True)
        # Failed or interrupted downloads never become a reusable final cache.
        with tempfile.TemporaryDirectory(prefix=f".{model['resolved_revision']}.", dir=model_path.parent) as temporary:
            staging = Path(temporary)
            _download_model(
                python, model_id=model["model_id"],
                revision=model["resolved_revision"], destination=staging,
            )
            snapshot = create_snapshot_manifest(
                staging, model_id=model["model_id"], revision=model["resolved_revision"],
            )
            files.path(model_path, directory=True)
            if model_path.exists():
                raise ValueError("semantic model path appeared during download; refusing to overwrite it")
            staging.rename(model_path)
    configure_local(
        plan["root"],
        model_path=model_path,
        device=plan["semantic_configuration"]["device"],
        trust_local_model_code=False,
        dimensions=model["dimensions"],
        auto_refresh=plan["semantic_configuration"]["auto_refresh"],
        snapshot=snapshot,
    )
    environment_cli = _environment_cli(env_path)
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "status": "configured",
        "plan_sha256": actual,
        "environment": {"manager": environment["manager"], "path": str(env_path)},
        "model": {
            "model_id": model["model_id"], "requested_revision": model["requested_revision"],
            "resolved_revision": snapshot.revision, "path": str(model_path),
            "snapshot": snapshot.to_dict(), "snapshot_verified": True,
        },
        "dependency_environment_locked": False,
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
