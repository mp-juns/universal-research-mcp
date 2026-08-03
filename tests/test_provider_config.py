from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_research_mcp.providers import ProviderConfigurationError
from universal_research_mcp.runtime.provider_config import (
    configure_local_embedding,
    configure_loopback_generation,
    configure_remote_provider,
    load_provider_config,
    provider_configuration_status,
)


def test_provider_config_stores_only_credential_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-must-never-be-stored"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    report = configure_remote_provider(
        tmp_path,
        capability="embedding",
        provider_id="openai",
        model="text-embedding-fixture",
        credential_ref="env:OPENAI_API_KEY",
    )
    payload = Path(report["path"]).read_text(encoding="utf-8")
    assert secret not in payload
    assert "env:OPENAI_API_KEY" in payload
    status = provider_configuration_status(tmp_path)
    rendered = json.dumps(status, sort_keys=True)
    assert secret not in rendered
    assert "OPENAI_API_KEY" not in rendered
    assert status["embedding"]["remote"]["credential_available"] is True


def test_provider_config_rejects_plaintext_and_anthropic_embedding(tmp_path: Path) -> None:
    with pytest.raises(ProviderConfigurationError):
        configure_remote_provider(
            tmp_path,
            capability="embedding",
            provider_id="openai",
            model="fixture",
            credential_ref="literal:secret",
        )
    with pytest.raises(ValueError):
        configure_remote_provider(
            tmp_path,
            capability="embedding",
            provider_id="anthropic",
            model="fixture",
            credential_ref="env:ANTHROPIC_API_KEY",
        )


def test_local_and_remote_configuration_coexist_without_authorizing_calls(tmp_path: Path) -> None:
    model = tmp_path / "cached-model"
    model.mkdir()
    configure_local_embedding(tmp_path, model_path=model, device="cpu")
    configure_remote_provider(
        tmp_path,
        capability="generation",
        provider_id="anthropic",
        model="generation-fixture",
        credential_ref="keyring:research/anthropic",
    )
    loaded = load_provider_config(tmp_path)
    assert loaded["embedding"]["local"]["model_path"] == str(model)
    assert loaded["generation"]["remote"]["provider_id"] == "anthropic"
    status = provider_configuration_status(tmp_path)
    assert status["generation"]["remote"]["requires_per_run_approval_and_budget"] is True


def test_loopback_generation_config_is_no_auth_by_default_and_never_connects(tmp_path: Path) -> None:
    report = configure_loopback_generation(
        tmp_path,
        endpoint="http://127.0.0.1:11434/v1",
        model="qwen-fixture",
    )
    loaded = load_provider_config(tmp_path)
    assert loaded["generation"]["loopback"] == {
        "provider_id": "openai-compatible-loopback",
        "endpoint": "http://127.0.0.1:11434/v1",
        "model": "qwen-fixture",
        "credential_ref": None,
    }
    status = provider_configuration_status(tmp_path)["generation"]["loopback"]
    assert status["credential_configured"] is False
    assert status["credential_kind"] is None
    assert status["requires_per_run_approval_and_budget"] is True
    assert Path(report["path"]).is_file()


def test_loopback_generation_config_hides_optional_credential_locator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "local-secret-never-render"
    monkeypatch.setenv("LOCAL_LLM_API_KEY", secret)
    report = configure_loopback_generation(
        tmp_path,
        endpoint="http://[::1]:8080/v1",
        model="local-fixture",
        credential_ref="env:LOCAL_LLM_API_KEY",
    )
    stored = Path(report["path"]).read_text(encoding="utf-8")
    assert secret not in stored
    assert "env:LOCAL_LLM_API_KEY" in stored
    rendered_status = json.dumps(provider_configuration_status(tmp_path), sort_keys=True)
    assert secret not in rendered_status
    assert "LOCAL_LLM_API_KEY" not in rendered_status


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434/v1",
        "http://192.168.1.10:11434/v1",
        "https://127.0.0.1:11434/v1",
    ],
)
def test_loopback_generation_config_rejects_non_loopback_endpoint(
    tmp_path: Path,
    endpoint: str,
) -> None:
    with pytest.raises(ProviderConfigurationError):
        configure_loopback_generation(tmp_path, endpoint=endpoint, model="fixture")


def test_loopback_generation_config_rejects_plaintext_credential(tmp_path: Path) -> None:
    with pytest.raises(ProviderConfigurationError):
        configure_loopback_generation(
            tmp_path,
            endpoint="http://127.0.0.1:11434/v1",
            model="fixture",
            credential_ref="literal:secret",
        )
