"""Tests for explicit semantic environment/model setup gates."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from universal_research_mcp import cli
from universal_research_mcp.runtime import semantic_setup
from universal_research_mcp.runtime.model_snapshot import MANIFEST_NAME, read_snapshot_identity, verify_snapshot
from universal_research_mcp.runtime.semantic_config import load_semantic_config
from universal_research_mcp.server import configure_runtime, research_semantic_models, research_semantic_setup_plan


MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "a" * 40


@pytest.fixture
def mocked_setup(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {"commands": [], "downloads": []}
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda _name: None)

    def fake_run(command: list[str]) -> None:
        calls["commands"].append(command)
        if command[1:3] == ["-m", "venv"]:
            python = semantic_setup._environment_python(Path(command[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")

    def fake_download(python: Path, *, model_id: str, revision: str, destination: Path) -> None:
        assert python.is_file()
        assert model_id == MODEL_ID
        assert revision == REVISION
        calls["downloads"].append(destination)
        (destination / "config.json").write_text("{}", encoding="utf-8")
        (destination / "model.safetensors").write_bytes(b"fixture-weights")
        bookkeeping = destination / ".cache/huggingface/download/config.json.metadata"
        bookkeeping.parent.mkdir(parents=True)
        bookkeeping.write_text("download metadata", encoding="utf-8")

    monkeypatch.setattr(semantic_setup, "_run", fake_run)
    monkeypatch.setattr(semantic_setup, "_download_model", fake_download)
    return calls


def _plan(root: Path, **kwargs) -> dict:
    return semantic_setup.setup_plan(root, model_id=MODEL_ID, revision=REVISION, manager="venv", **kwargs)


def _execute(plan: dict) -> dict:
    return semantic_setup.execute_setup(plan, confirm_plan_sha256=plan["plan_sha256"])


def test_catalogue_has_ten_reviewed_models_and_no_network() -> None:
    report = semantic_setup.catalogue()
    assert report["model_count"] == 10
    assert report["network_used"] is False
    assert sum(model["recommended"] for model in report["models"]) == 1
    assert any(model["model_id"] == "intfloat/multilingual-e5-base" for model in report["models"])


def test_mcp_exposes_only_read_only_model_catalogue_and_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda _name: None)
    configure_runtime(tmp_path)
    assert research_semantic_models()["model_count"] == 10
    plan = research_semantic_setup_plan(
        "intfloat/multilingual-e5-base", revision=REVISION, environment_manager="venv", device="cuda",
    )
    assert plan["status"] == "confirmation_required"
    assert plan["model_execution"] is False
    assert not (tmp_path / ".universal-research").exists()


def test_setup_plan_prefers_conda_when_available_without_creating_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda name: "/opt/conda/bin/conda" if name == "conda" else None)
    plan = semantic_setup.setup_plan(tmp_path, model_id="intfloat/multilingual-e5-base", revision=REVISION)
    assert plan["status"] == "confirmation_required"
    assert plan["environment"]["manager"] == "conda"
    assert plan["environment"]["state"] == "will_create"
    assert plan["model"]["state"] == "will_download"
    assert plan["network"]["required_on_execute"] is True
    assert plan["model"]["resolved_revision"] == REVISION
    other = semantic_setup.setup_plan(tmp_path, model_id="intfloat/multilingual-e5-base", revision="b" * 40)
    assert other["model"]["path"] != plan["model"]["path"]
    assert other["plan_sha256"] != plan["plan_sha256"]
    assert not (tmp_path / ".universal-research").exists()


def test_setup_cli_prints_plan_and_refuses_missing_or_wrong_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda _name: None)
    args = [
        "semantic", "setup", "--root", str(tmp_path),
        "--model", "sentence-transformers/all-MiniLM-L6-v2", "--environment-manager", "venv",
        "--revision", REVISION,
    ]
    assert cli.main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["environment"]["manager"] == "venv"
    assert not (tmp_path / ".universal-research").exists()
    with pytest.raises(ValueError, match="requires --confirm"):
        cli.main([*args, "--execute"])
    with pytest.raises(ValueError, match="confirm-plan-sha256"):
        cli.main([*args, "--execute", "--confirm-plan-sha256", "0" * 64])
    assert not (tmp_path / ".universal-research").exists()


def test_execute_setup_runs_only_exact_planned_operations(
    tmp_path: Path, mocked_setup: dict[str, list],
) -> None:
    plan = _plan(tmp_path, device="cpu")
    report = _execute(plan)
    assert report["status"] == "configured"
    assert report["index_build_executed"] is False
    assert len(mocked_setup["commands"]) == 3
    assert len(mocked_setup["downloads"]) == 1
    assert mocked_setup["downloads"][0] != Path(plan["model"]["path"])
    config = load_semantic_config(tmp_path)
    assert config is not None
    assert config["backend"]["kind"] == "local_sentence_transformer"
    assert config["backend"]["device"] == "cpu"
    assert config["backend"]["snapshot"] == report["model"]["snapshot"]
    snapshot_path = Path(plan["model"]["path"])
    verify_snapshot(snapshot_path, read_snapshot_identity(snapshot_path))
    assert report["model"]["resolved_revision"] == REVISION
    assert report["dependency_environment_locked"] is False


def test_execute_setup_rejects_tampered_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda _name: None)
    plan = semantic_setup.setup_plan(tmp_path, model_id="BAAI/bge-m3", manager="venv", revision=REVISION)
    plan["model"]["dimensions"] = 1
    with pytest.raises(ValueError, match="confirm-plan-sha256"):
        semantic_setup.execute_setup(plan, confirm_plan_sha256=plan["plan_sha256"])


@pytest.mark.parametrize("revision", ["main", "v1.0", "a" * 7, "g" * 40, "../model", "", None, True])
def test_plan_rejects_mutable_or_invalid_revisions_without_writes(tmp_path: Path, revision) -> None:
    with pytest.raises(ValueError, match="full 40-character commit SHA"):
        semantic_setup.setup_plan(tmp_path, model_id=MODEL_ID, revision=revision)
    assert list(tmp_path.iterdir()) == []


def test_cli_requires_revision_and_mcp_rejects_a_branch(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["semantic", "setup", "--root", str(tmp_path), "--model", MODEL_ID])
    assert error.value.code == 2
    with pytest.raises(ValueError, match="full 40-character commit SHA"):
        research_semantic_setup_plan(MODEL_ID, revision="main")
    assert list(tmp_path.iterdir()) == []


def test_verified_cache_reuse_does_not_download_model(tmp_path: Path, mocked_setup: dict[str, list]) -> None:
    original = _execute(_plan(tmp_path))
    snapshot_path = Path(original["model"]["path"])
    metadata = snapshot_path / ".cache/huggingface/download/config.json.metadata"
    metadata.write_text("non-model metadata changed", encoding="utf-8")
    mocked_setup["commands"].clear()
    mocked_setup["downloads"].clear()
    plan = _plan(tmp_path, reuse_existing=True)
    assert plan == _plan(tmp_path, reuse_existing=True)
    report = _execute(plan)
    assert mocked_setup["downloads"] == []
    assert len(mocked_setup["commands"]) == 2  # Existing package-install policy is unchanged.
    assert report["model"]["snapshot"] == original["model"]["snapshot"]


@pytest.mark.parametrize("change", ["modify", "delete", "add", "missing_manifest", "wrong_revision", "wrong_model", "invalid_json", "manifest_bytes"])
def test_cache_changes_fail_before_install_or_configuration(
    tmp_path: Path, mocked_setup: dict[str, list], change: str,
) -> None:
    original = _execute(_plan(tmp_path))
    plan = _plan(tmp_path, reuse_existing=True)
    path = Path(original["model"]["path"])
    config_path = tmp_path / "config/semantic.json"
    before = config_path.read_bytes()
    manifest_path = path / MANIFEST_NAME
    if change == "modify":
        (path / "model.safetensors").write_bytes(b"changed-weights")
    elif change == "delete":
        (path / "config.json").unlink()
    elif change == "add":
        (path / "extra.json").write_text("{}", encoding="utf-8")
    elif change == "missing_manifest":
        manifest_path.unlink()
    elif change in {"wrong_revision", "wrong_model"}:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["revision" if change == "wrong_revision" else "model_id"] = "b" * 40 if change == "wrong_revision" else "BAAI/bge-m3"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif change == "invalid_json":
        manifest_path.write_text("{", encoding="utf-8")
    else:
        manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    mocked_setup["commands"].clear()
    mocked_setup["downloads"].clear()
    with pytest.raises(ValueError, match="snapshot|plan"):
        _execute(plan)
    assert mocked_setup == {"commands": [], "downloads": []}
    assert config_path.read_bytes() == before
    assert not (tmp_path / ".universal-research/.semantic-setup.lock").exists()


def test_cache_without_manifest_is_not_blessed_by_reuse_flag(tmp_path: Path, mocked_setup: dict[str, list]) -> None:
    plan = _plan(tmp_path)
    path = Path(plan["model"]["path"])
    path.mkdir(parents=True)
    (path / "model.safetensors").write_bytes(b"unknown model")
    with pytest.raises(ValueError, match="manifest is missing"):
        _plan(tmp_path, reuse_existing=True)
    assert mocked_setup == {"commands": [], "downloads": []}
    assert not (path / MANIFEST_NAME).exists()


@pytest.mark.parametrize("target", ["environment", "model"])
def test_path_appearing_after_plan_requires_new_confirmation(
    tmp_path: Path, mocked_setup: dict[str, list], target: str,
) -> None:
    plan = _plan(tmp_path)
    Path(plan[target]["path"]).mkdir(parents=True)
    with pytest.raises(ValueError, match="state changed"):
        _execute(plan)
    assert mocked_setup == {"commands": [], "downloads": []}


@pytest.mark.parametrize("change", ["schema", "destination", "package", "model_id"])
def test_rehashed_plan_cannot_bypass_current_planner_contract(
    tmp_path: Path, mocked_setup: dict[str, list], change: str,
) -> None:
    plan = _plan(tmp_path)
    if change == "schema":
        plan["schema_version"] = "semantic-local-setup/1.0"
    elif change == "destination":
        plan["model"]["path"] = str(tmp_path / "unapproved")
    elif change == "package":
        plan["package"]["requirement"] = "other-package"
    else:
        plan["model"]["model_id"] = "unreviewed/model"
    plan.pop("plan_sha256")
    plan["plan_sha256"] = semantic_setup._sha256(plan)
    with pytest.raises(ValueError, match="plan|catalogue"):
        _execute(plan)
    assert mocked_setup == {"commands": [], "downloads": []}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["interrupted", "empty"])
def test_failed_download_never_becomes_a_reusable_cache(
    tmp_path: Path, mocked_setup: dict[str, list], monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    plan = _plan(tmp_path)
    destinations = []

    def incomplete_download(_python: Path, *, destination: Path, **_kwargs) -> None:
        destinations.append(destination)
        if failure == "interrupted":
            (destination / "partial.bin").write_bytes(b"partial")
            raise RuntimeError("download interrupted")

    monkeypatch.setattr(semantic_setup, "_download_model", incomplete_download)
    with pytest.raises((ValueError, RuntimeError), match="interrupted|no model files"):
        _execute(plan)
    assert not Path(plan["model"]["path"]).exists()
    assert not destinations[0].exists()
    assert not (tmp_path / "config/semantic.json").exists()
    assert _plan(tmp_path, reuse_existing=True)["model"]["state"] == "will_download"


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink", "directory", "manifest"])
def test_cache_links_are_refused_before_external_operations(
    tmp_path: Path, mocked_setup: dict[str, list], link_kind: str,
) -> None:
    original = _execute(_plan(tmp_path))
    plan = _plan(tmp_path, reuse_existing=True)
    path = Path(original["model"]["path"])
    target = path / (MANIFEST_NAME if link_kind == "manifest" else "model.safetensors")
    outside = tmp_path / "outside-fixture"
    outside.mkdir()
    original_bytes = target.read_bytes()
    external = outside / target.name
    external.write_bytes(original_bytes)
    if link_kind != "directory":
        target.unlink()
    try:
        if link_kind == "hardlink":
            os.link(external, target)
        elif link_kind == "directory":
            (path / "linked-directory").symlink_to(outside, target_is_directory=True)
        else:
            target.symlink_to(external)
    except OSError as error:
        pytest.skip(f"link creation unavailable: {error}")
    mocked_setup["commands"].clear()
    mocked_setup["downloads"].clear()
    with pytest.raises(ValueError, match="symlink|single-link|reparse"):
        _execute(plan)
    assert mocked_setup == {"commands": [], "downloads": []}
    assert external.read_bytes() == original_bytes


def test_existing_setup_lock_is_not_removed_or_ignored(tmp_path: Path, mocked_setup: dict[str, list]) -> None:
    plan = _plan(tmp_path)
    lock = tmp_path / ".universal-research/.semantic-setup.lock"
    lock.parent.mkdir()
    lock.write_text("another setup", encoding="utf-8")
    with pytest.raises(RuntimeError, match="lock exists"):
        _execute(plan)
    assert mocked_setup == {"commands": [], "downloads": []}
    assert lock.read_text(encoding="utf-8") == "another setup"
