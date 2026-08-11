from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from universal_research_mcp.cli import build_parser, legacy_main, main
from universal_research_mcp.providers import ProviderConfigurationError


@pytest.fixture(autouse=True)
def _enable_internal_provider_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep prototype coverage without exposing these commands by default."""

    monkeypatch.setenv("UNIVERSAL_RESEARCH_INTERNAL_PROVIDER_PREVIEW", "1")


def test_default_cli_surface_is_codex_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNIVERSAL_RESEARCH_INTERNAL_PROVIDER_PREVIEW", raising=False)
    parser = build_parser()
    help_text = parser.format_help()
    assert "provider" not in help_text
    assert "harness" not in help_text
    assert "agent" not in help_text
    with pytest.raises(SystemExit):
        parser.parse_args(["provider", "status"])


def test_init_creates_queryable_empty_lexical_database_without_semantic_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "research"
    assert main(["init", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["lexical"]["status"] == "current"
    assert report["semantic"]["status"] == "missing"
    assert (root / "data/events/sources.jsonl").read_text(encoding="utf-8") == ""
    with sqlite3.connect(root / "data/index/research.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert not (root / "data/index/semantic.sqlite").exists()


def test_legacy_entrypoint_accepts_new_init_subcommand(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "legacy"
    assert legacy_main(["init", str(root)]) == 0
    capsys.readouterr()
    assert (root / "data/index/research.sqlite").is_file()


def test_cli_provider_configuration_never_accepts_raw_api_key_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([
        "provider", "configure-remote", "--root", str(tmp_path),
        "--capability", "embedding", "--provider", "openai",
        "--model", "text-embedding-fixture",
        "--credential-ref", "env:OPENAI_API_KEY",
    ]) == 0
    capsys.readouterr()
    assert main(["provider", "status", "--root", str(tmp_path)]) == 0
    rendered = capsys.readouterr().out
    assert "OPENAI_API_KEY" not in rendered
    assert "secret_values_exposed" in rendered
    with pytest.raises(SystemExit):
        build_parser().parse_args([
            "provider", "configure-remote", "--capability", "embedding",
            "--provider", "openai", "--model", "fixture",
            "--credential-ref", "env:OPENAI_API_KEY", "--api-key", "forbidden",
        ])


def test_cli_configures_only_a_literal_loopback_endpoint_without_contacting_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([
        "provider", "configure-loopback-generation", "--root", str(tmp_path),
        "--endpoint", "http://127.0.0.1:11434/v1",
        "--model", "local-fixture",
    ]) == 0
    configured = json.loads(capsys.readouterr().out)
    assert configured["provider_id"] == "openai-compatible-loopback"
    assert main(["provider", "status", "--root", str(tmp_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    loopback = status["generation"]["loopback"]
    assert loopback["network_scope"] == "loopback"
    assert loopback["credential_configured"] is False

    with pytest.raises(ProviderConfigurationError):
        main([
            "provider", "configure-loopback-generation", "--root", str(tmp_path),
            "--endpoint", "http://localhost:11434/v1", "--model", "blocked",
        ])


def _agent_arguments(action: str, packets: Path) -> list[str]:
    return [
        "agent", action, str(packets),
        "--root", str(packets.parent),
        "--route", "loopback",
        "--approval-ref", "approval_fixture",
        "--max-workers", "2",
        "--max-calls", "2",
        "--max-input-tokens", "10000",
        "--max-total-output-tokens", "1000",
        "--max-output-tokens-per-agent", "500",
        "--max-cost-usd", "0",
        "--input-cost-per-million-tokens-usd", "0",
        "--output-cost-per-million-tokens-usd", "0",
        "--timeout-seconds", "30",
    ]


def test_agent_cli_run_requires_explicit_flag_before_reading_packets_or_building_route(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import universal_research_mcp.agent_execution as agent_execution

    monkeypatch.setattr(
        agent_execution,
        "build_generation_executor",
        lambda *_args, **_kwargs: pytest.fail("route must not be built"),
    )
    missing = tmp_path / "not-read.json"
    assert main(_agent_arguments("run", missing)) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    assert report["executed"] is False


def test_agent_cli_approve_rechecks_exact_preflight_and_creates_local_grant_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import universal_research_mcp.agent_execution as agent_execution
    import universal_research_mcp.agent_runtime as agent_runtime
    import universal_research_mcp.runtime.agent_approval as agent_approval

    packets = tmp_path / "packets.json"
    packets.write_text(json.dumps([{"agent_id": "fixture"}]), encoding="utf-8")
    calls: list[str] = []

    class _Executor:
        def usage_snapshot(self) -> dict[str, int]:
            return {"provider_calls_reserved": 0}

    class _Bundle:
        provider_id = "openai-compatible-loopback"
        model = "fixture-model"
        network_scope = "loopback"
        provider_configuration_hash = "sha256:provider"
        executor = _Executor()

        def summary(self) -> dict[str, object]:
            return {"provider_id": self.provider_id, "credential_values_exposed": False}

    class _Configuration:
        def __init__(self, **values: object) -> None:
            self.values = values

        def to_dict(self) -> dict[str, object]:
            return dict(self.values)

    class _Runtime:
        def __init__(self, root: Path, executor: object, *, approval_validator: object) -> None:
            assert root == tmp_path.resolve()
            assert isinstance(executor, _Executor)
            assert callable(approval_validator)

        def preflight(self, loaded: list[dict], configuration: _Configuration) -> dict[str, object]:
            calls.append("preflight")
            assert loaded == [{"agent_id": "fixture"}]
            assert configuration.values["provider_configuration_hash"] == "sha256:provider"
            return {
                "valid": True,
                "issues": [],
                "run_plan": {"run_id": "run_fixture", "run_plan_hash": "sha256:plan"},
                "run_plan_hash": "sha256:plan",
                "estimate_snapshot_hash": "sha256:estimate",
                "execution_request_hash": "sha256:execution",
            }

    class _ApprovalStore:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path.resolve()

        def consume(self, *_args: object) -> dict[str, object]:
            raise AssertionError("approve must not consume the grant")

        def create(
            self,
            plan: dict[str, object],
            configuration: _Configuration,
            *,
            expected_run_plan_hash: str,
            expected_execution_request_hash: str,
            expires_at: str,
            estimate_snapshot_hash: str,
            execution_request_hash: str,
        ) -> dict[str, object]:
            calls.append("create")
            assert plan["run_plan_hash"] == expected_run_plan_hash == "sha256:plan"
            assert expected_execution_request_hash == "sha256:execution"
            assert configuration.values["approval_ref"] == "approval_fixture"
            assert expires_at == "2099-01-01T00:00:00+00:00"
            assert estimate_snapshot_hash == "sha256:estimate"
            assert execution_request_hash == "sha256:execution"
            return {
                "schema_version": "agent-execution-approval/2.0",
                "approval_ref": "approval_fixture",
                "run_plan_hash": "sha256:plan",
                "credential_values_exposed": False,
            }

    monkeypatch.setattr(agent_execution, "build_generation_executor", lambda *_args, **_kwargs: _Bundle())
    monkeypatch.setattr(agent_runtime, "RunConfiguration", _Configuration)
    monkeypatch.setattr(agent_runtime, "AgentRuntime", _Runtime)
    monkeypatch.setattr(agent_approval, "AgentApprovalStore", _ApprovalStore)
    arguments = [
        *_agent_arguments("approve", packets),
        "--expected-run-plan-hash", "sha256:plan",
        "--expected-execution-request-hash", "sha256:execution",
        "--expires-at", "2099-01-01T00:00:00+00:00",
    ]
    assert main(arguments) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "approved"
    assert report["executed"] is False
    assert report["artifact_contents_included"] is False
    assert calls == ["preflight", "create"]


def test_agent_cli_has_no_api_key_argument(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([
            *_agent_arguments("preflight", tmp_path / "packets.json"),
            "--api-key", "forbidden",
        ])


def test_legacy_harness_run_is_fail_closed_before_packet_or_provider_access(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([
        "harness", "run", str(tmp_path / "not-read.json"), "--root", str(tmp_path),
    ]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    assert report["executed"] is False
    assert "agent preflight" in report["reason"]


def test_agent_cli_inspect_whitelists_inventory_and_never_returns_artifact_contents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import universal_research_mcp.agent_runtime as agent_runtime

    class _Store:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path.resolve()

        def inspect(self, run_id: str, agent_id: str | None) -> dict[str, object]:
            return {
                "status": {"run_id": run_id, "state": "completed"},
                "manifest": {
                    "run_id": run_id,
                    "agent_ids": [agent_id],
                    "raw_output": "hidden",
                },
                "run_plan": {"run_plan_hash": "sha256:plan", "prompt": "hidden"},
                "sessions": [{
                    "session_id": "session_fixture",
                    "agent_id": agent_id,
                    "artifact_names": ["prompt.json", "raw-output.json"],
                    "decision": {
                        "status": "pass",
                        "summary": "secret model-authored prose",
                        "decision_hash": "sha256:decision",
                        "finding_count": 2,
                        "evidence_reference_count": 3,
                        "raw_output": "hidden",
                    },
                    "prompt": "hidden",
                }],
                "prompt": "hidden",
            }

        def run_dir(self, run_id: str) -> Path:
            return tmp_path / "data/governance/runs" / run_id

    monkeypatch.setattr(agent_runtime, "SessionStore", _Store)
    assert main([
        "agent", "inspect", "run_fixture", "--root", str(tmp_path),
        "--agent-id", "retrieval_governor",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    rendered = json.dumps(report, sort_keys=True)
    assert "hidden" not in rendered
    assert report["artifact_contents_included"] is False
    assert report["sessions"][0]["decision"] == {
        "status": "pass",
        "decision_hash": "sha256:decision",
        "finding_count": 2,
        "evidence_reference_count": 3,
    }
    assert "secret model-authored prose" not in rendered
