"""Tests for declarative research-profile routing policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_research_mcp import cli
from universal_research_mcp.runtime.research_profile import (
    configuration_path,
    profile_template,
    validate_profile,
)
from universal_research_mcp.semantic_runtime import configured_backend
from universal_research_mcp.server import configure_runtime, research_profile_status


def _write_profile(path: Path, profile: dict[str, object]) -> None:
    path.write_text(json.dumps(profile), encoding="utf-8")


def test_template_is_valid_and_does_not_activate_semantic_or_network() -> None:
    profile = validate_profile(profile_template())
    assert profile["retrieval"]["mode"] == "lexical"
    assert profile["retrieval"]["semantic_backend"] == {"kind": "disabled"}
    assert profile["provider_policy"]["network_enabled"] is False


def test_profile_cli_validates_then_applies_only_matching_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["init", str(tmp_path)]) == 0
    capsys.readouterr()
    profile_path = tmp_path / "profile.json"
    profile = profile_template()
    profile["retrieval"] = {
        "mode": "semantic",
        "semantic_backend": {"kind": "demo", "dimensions": 64, "auto_refresh": False},
    }
    _write_profile(profile_path, profile)

    assert cli.main(["profile", "validate", str(profile_path), "--root", str(tmp_path)]) == 0
    validated = json.loads(capsys.readouterr().out)
    digest = validated["profile_sha256"]
    assert validated["execution"] == "not_executed"
    assert not configuration_path(tmp_path).exists()

    with pytest.raises(ValueError, match="confirm-profile-sha256"):
        cli.main([
            "profile", "apply", str(profile_path), "--root", str(tmp_path),
            "--confirm-profile-sha256", "0" * 64,
        ])
    assert not configuration_path(tmp_path).exists()

    assert cli.main([
        "profile", "apply", str(profile_path), "--root", str(tmp_path),
        "--confirm-profile-sha256", digest,
    ]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["profile_sha256"] == digest
    assert applied["provider_execution"] == "not_supported_by_public_mcp"
    assert configuration_path(tmp_path).is_file()

    backend = configured_backend(tmp_path)
    assert backend is not None
    assert backend.provider_id == "deterministic_demo"
    assert backend.dimensions == 64


@pytest.mark.parametrize(
    "mutate, error",
    [
        (
            lambda profile: profile["provider_policy"].update({
                "allowed_routes": [{"kind": "openai", "model": "text-embedding-3-small", "credential_ref": "env:OPENAI_API_KEY"}],
            }),
            "requires network_enabled",
        ),
        (
            lambda profile: profile["execution"].update({"registered_skill_ids": ["invent-a-skill"]}),
            "registered_skill_ids",
        ),
        (
            lambda profile: profile["provider_policy"].update({
                "allowed_routes": [{"kind": "openai", "model": "text-embedding-3-small", "credential_ref": "sk-secret"}],
                "network_enabled": True,
            }),
            "credential_ref",
        ),
        (
            lambda profile: profile["source_scope"].update({"exclude_secrets": False}),
            "must exclude secrets",
        ),
    ],
)
def test_profile_rejects_unbounded_or_secret_bearing_routes(mutate, error: str) -> None:
    profile = profile_template()
    mutate(profile)
    with pytest.raises(ValueError, match=error):
        validate_profile(profile)


def test_mcp_profile_status_is_read_only_and_uses_configured_root(tmp_path: Path) -> None:
    configure_runtime(tmp_path)
    missing = research_profile_status()
    assert missing["status"] == "missing"
    assert missing["provider_execution"] == "not_supported_by_public_mcp"

    from universal_research_mcp.runtime.research_profile import write_profile

    write_profile(tmp_path, profile_template())
    configured = research_profile_status()
    assert configured["status"] == "configured"
    assert configured["execution"] == "declarative_only"
    assert configured["subagent_execution"] == "host_governed_only"
