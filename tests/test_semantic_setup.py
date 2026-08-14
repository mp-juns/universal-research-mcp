"""Tests for explicit semantic environment/model setup gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_research_mcp import cli
from universal_research_mcp.runtime import semantic_setup
from universal_research_mcp.runtime.semantic_config import load_semantic_config
from universal_research_mcp.server import configure_runtime, research_semantic_models, research_semantic_setup_plan


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
        "intfloat/multilingual-e5-base", environment_manager="venv", device="cuda",
    )
    assert plan["status"] == "confirmation_required"
    assert plan["model_execution"] is False
    assert not (tmp_path / ".universal-research").exists()


def test_setup_plan_prefers_conda_when_available_without_creating_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda name: "/opt/conda/bin/conda" if name == "conda" else None)
    plan = semantic_setup.setup_plan(tmp_path, model_id="intfloat/multilingual-e5-base")
    assert plan["status"] == "confirmation_required"
    assert plan["environment"]["manager"] == "conda"
    assert plan["environment"]["state"] == "will_create"
    assert plan["model"]["state"] == "will_download"
    assert plan["network"]["required_on_execute"] is True
    assert not (tmp_path / ".universal-research").exists()


def test_setup_cli_prints_plan_and_refuses_missing_or_wrong_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda _name: None)
    args = [
        "semantic", "setup", "--root", str(tmp_path),
        "--model", "sentence-transformers/all-MiniLM-L6-v2", "--environment-manager", "venv",
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda _name: None)
    plan = semantic_setup.setup_plan(
        tmp_path,
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        manager="venv",
        device="cpu",
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        if command[1:3] == ["-m", "venv"]:
            environment = Path(command[-1])
            python = environment / ("Scripts/python.exe" if semantic_setup.os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")

    def fake_download(python: Path, *, model_id: str, revision: str, destination: Path) -> None:
        assert python.is_file()
        assert model_id == "sentence-transformers/all-MiniLM-L6-v2"
        assert revision == "main"
        destination.mkdir(parents=True)

    monkeypatch.setattr(semantic_setup, "_run", fake_run)
    monkeypatch.setattr(semantic_setup, "_download_model", fake_download)
    report = semantic_setup.execute_setup(plan, confirm_plan_sha256=plan["plan_sha256"])
    assert report["status"] == "configured"
    assert report["index_build_executed"] is False
    assert len(commands) == 3
    config = load_semantic_config(tmp_path)
    assert config is not None
    assert config["backend"]["kind"] == "local_sentence_transformer"
    assert config["backend"]["device"] == "cpu"


def test_execute_setup_rejects_tampered_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(semantic_setup.shutil, "which", lambda _name: None)
    plan = semantic_setup.setup_plan(tmp_path, model_id="BAAI/bge-m3", manager="venv")
    plan["model"]["dimensions"] = 1
    with pytest.raises(ValueError, match="confirm-plan-sha256"):
        semantic_setup.execute_setup(plan, confirm_plan_sha256=plan["plan_sha256"])
