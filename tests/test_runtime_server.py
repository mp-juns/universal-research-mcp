from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from universal_research_mcp import runtime_server
from universal_research_mcp.governance.hashing import artifact_hash, hash_without
from universal_research_mcp.runtime.agent_approval import (
    AgentApprovalError,
    AgentApprovalStore,
)
from universal_research_mcp.agent_runtime import AgentRuntime, RunConfiguration
from universal_research_mcp.agent_runtime.runtime import (
    build_estimate_snapshot,
    build_execution_request_hash,
)


class _Executor:
    def usage_snapshot(self) -> dict[str, int]:
        return {"provider_calls_reserved": 2}


class _Bundle:
    provider_id = "openai-compatible-loopback"
    model = "fixture-model"
    network_scope = "loopback"
    provider_configuration_hash = "sha256:provider"
    executor = _Executor()

    def summary(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "route": "loopback",
            "network_scope": self.network_scope,
            "provider_configuration_hash": self.provider_configuration_hash,
            "credential_values_exposed": False,
        }


class _Runtime:
    def preflight(self, packets: list[dict[str, Any]], configuration: object) -> dict[str, Any]:
        assert packets == [{"authority": {"approval_refs": ["approval_fixture"]}}]
        assert configuration == "configuration"
        return {
            "schema_version": "agent-runtime-preflight/1.0",
            "valid": True,
            "issues": [],
            "run_id": "run_fixture",
            "run_plan": {"run_plan_hash": "sha256:plan"},
            "run_plan_hash": "sha256:plan",
            "estimate_snapshot_hash": "sha256:estimate",
            "execution_request_hash": "sha256:execution",
            "estimates": {"scope_and_cost_governor": {"estimated_input_tokens": 10}},
            "executed": False,
            "prompt": "must-not-leak",
            "raw_output": "must-not-leak",
        }

    def run(self, packets: list[dict[str, Any]], configuration: object) -> dict[str, Any]:
        assert packets == [{"authority": {"approval_refs": ["approval_fixture"]}}]
        assert configuration == "configuration"
        return {
            "schema_version": "agent-runtime-run/1.0",
            "run_id": "run_fixture",
            "run_plan_hash": "sha256:plan",
            "estimate_snapshot_hash": "sha256:estimate",
            "execution_request_hash": "sha256:execution",
            "status": "completed",
            "reason": "completed",
            "claim_eligibility": "eligible",
            "agent_result_count": 2,
            "failure_count": 0,
            "pending_failure_record_choices": 1,
            "user_choice_required": True,
            "event_head_hash": "sha256:event",
            "executed": True,
            "hidden_retries": 0,
            "prompt": "must-not-leak",
            "raw_output": "must-not-leak",
        }


def _runtime_kwargs(*, execution_approved: bool | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {
        "packets": [{"authority": {"approval_refs": ["approval_fixture"]}}],
        "route": "loopback",
        "approval_ref": "approval_fixture",
        "max_workers": 2,
        "max_calls": 2,
        "max_input_tokens": 10_000,
        "max_output_tokens": 1_000,
        "max_output_tokens_per_agent": 500,
        "max_cost_usd": 0.0,
        "input_cost_per_million_tokens_usd": 0.0,
        "output_cost_per_million_tokens_usd": 0.0,
        "timeout_seconds": 30.0,
    }
    if execution_approved is not None:
        values["execution_approved"] = execution_approved
    return values


def test_runtime_mcp_exposes_only_secret_free_runtime_arguments() -> None:
    tools = {tool.name: tool for tool in asyncio.run(runtime_server.mcp.list_tools())}
    assert {
        "agent_runtime_preflight",
        "agent_runtime_run",
        "agent_runtime_status",
        "agent_runtime_inspect",
    } <= set(tools)
    assert not any("approve" in name for name in tools)
    rendered = str(tools["agent_runtime_run"].inputSchema).lower()
    assert "api_key" not in rendered
    assert "credential_ref" not in rendered
    assert "execution_approved" in rendered


def test_runtime_mcp_run_is_blocked_before_route_construction_without_both_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runtime_server,
        "_runtime_components",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.delenv(runtime_server.EXECUTION_ENABLE_ENV, raising=False)
    disabled = runtime_server.agent_runtime_run(**_runtime_kwargs(execution_approved=True))
    assert disabled["status"] == "blocked"
    assert disabled["executed"] is False
    assert calls == []

    monkeypatch.setenv(runtime_server.EXECUTION_ENABLE_ENV, "1")
    unapproved = runtime_server.agent_runtime_run(**_runtime_kwargs(execution_approved=False))
    assert unapproved["status"] == "blocked"
    assert unapproved["executed"] is False
    assert calls == []


def test_runtime_mcp_preflight_and_run_hide_prompts_and_raw_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_server,
        "_runtime_components",
        lambda **_kwargs: (_Runtime(), "configuration", _Bundle()),
    )
    preflight = runtime_server.agent_runtime_preflight(**_runtime_kwargs())
    assert preflight["valid"] is True
    assert preflight["executed"] is False
    assert preflight["estimate_snapshot_hash"] == "sha256:estimate"
    assert preflight["execution_request_hash"] == "sha256:execution"
    assert preflight["artifact_contents_included"] is False
    assert "prompt" not in preflight
    assert "raw_output" not in preflight

    monkeypatch.setenv(runtime_server.EXECUTION_ENABLE_ENV, "1")
    completed = runtime_server.agent_runtime_run(**_runtime_kwargs(execution_approved=True))
    assert completed["status"] == "completed"
    assert completed["executed"] is True
    assert completed["estimate_snapshot_hash"] == "sha256:estimate"
    assert completed["execution_request_hash"] == "sha256:execution"
    assert completed["artifact_contents_included"] is False
    assert completed["pending_failure_record_choices"] == 1
    assert completed["user_choice_required"] is True
    assert "prompt" not in completed
    assert "raw_output" not in completed


def test_runtime_mcp_status_and_inspect_return_inventory_without_artifact_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Store:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path.resolve()

        def status(self, run_id: str) -> dict[str, Any]:
            return {
                "run_id": run_id,
                "state": "completed",
                "sessions": {"session_fixture": "completed"},
                "event_count": 9,
                "event_head_hash": "sha256:event",
            }

        def inspect(self, run_id: str, agent_id: str | None) -> dict[str, Any]:
            assert agent_id == "retrieval_governor"
            return {
                "status": self.status(run_id),
                "manifest": {"run_id": run_id, "agent_ids": [agent_id], "secret": "hidden"},
                "run_plan": {"run_plan_hash": "sha256:plan"},
                "sessions": [{
                    "agent_id": agent_id,
                    "artifact_names": ["prompt.json", "raw-output.json", "decision.json"],
                    "decision": {
                        "status": "pass",
                        "summary": "secret model-authored prose",
                        "decision_hash": "sha256:decision",
                        "finding_count": 2,
                        "evidence_reference_count": 3,
                    },
                }],
            }

        def run_dir(self, run_id: str) -> Path:
            return tmp_path / "data/governance/runs" / run_id

    runtime_server.configure_runtime(tmp_path)
    monkeypatch.setattr(runtime_server, "SessionStore", _Store)
    status = runtime_server.agent_runtime_status("run_fixture")
    assert status["state"] == "completed"
    assert status["artifact_contents_included"] is False

    inspected = runtime_server.agent_runtime_inspect(
        "run_fixture", "retrieval_governor",
    )
    assert inspected["manifest"] == {
        "run_id": "run_fixture",
        "agent_ids": ["retrieval_governor"],
    }
    assert inspected["artifact_contents_included"] is False
    assert "secret model-authored prose" not in str(inspected)
    assert "secret" not in inspected["manifest"]
    assert "prompt" not in inspected
    assert "raw_output" not in inspected


class _Configuration:
    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": "openai-compatible-loopback",
            "model": "fixture-model",
            "network_scope": "loopback",
            "provider_configuration_hash": "sha256:provider",
            "approval_ref": "approval_fixture",
            "max_workers": 2,
            "budgets": {
                "max_calls": 2,
                "max_input_tokens": 10_000,
                "max_output_tokens": 1_000,
                "max_output_tokens_per_agent": 500,
                "max_cost_usd": 0.0,
                "timeout_seconds": 30.0,
            },
        }


def _run_plan(configuration: object) -> dict[str, Any]:
    assert callable(getattr(configuration, "to_dict", None))
    config = configuration.to_dict()
    plan = {
        "schema_version": "agent-run-plan/1.0",
        "run_id": "run_fixture",
        "workflow_id": "workflow_fixture",
        "configuration": config,
        "configuration_hash": artifact_hash(config),
        "tasks": [],
    }
    plan["run_plan_hash"] = hash_without(plan, "run_plan_hash")
    return plan


def _approval_store(tmp_path: Path) -> tuple[AgentApprovalStore, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    host_state = tmp_path / "host-state"
    return AgentApprovalStore(project, state_root=host_state), project, host_state


def _approval_material(
    plan: dict[str, Any], configuration: object,
) -> tuple[dict[str, Any], str, str]:
    assert callable(getattr(configuration, "to_dict", None))
    snapshot = build_estimate_snapshot({})
    snapshot_hash = artifact_hash(snapshot)
    request_hash = build_execution_request_hash(
        run_plan_hash=plan["run_plan_hash"],
        estimate_snapshot_hash=snapshot_hash,
        configuration_hash=artifact_hash(configuration.to_dict()),
    )
    return snapshot, snapshot_hash, request_hash


def _create_grant(
    store: AgentApprovalStore,
    plan: dict[str, Any],
    configuration: object,
    *,
    expected_run_plan_hash: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    _snapshot, snapshot_hash, request_hash = _approval_material(plan, configuration)
    return store.create(
        plan,
        configuration,
        expected_run_plan_hash=expected_run_plan_hash or plan["run_plan_hash"],
        expected_execution_request_hash=request_hash,
        expires_at=expires_at or (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat(),
        estimate_snapshot_hash=snapshot_hash,
        execution_request_hash=request_hash,
    )


def _consume_grant(
    store: AgentApprovalStore,
    plan: dict[str, Any],
    configuration: object,
    packets: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    snapshot, _snapshot_hash, request_hash = _approval_material(plan, configuration)
    return store.consume(plan, configuration, packets, snapshot, request_hash)


def test_execution_approval_uses_explicit_or_xdg_host_state_outside_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    host_state = tmp_path / "host-state"
    explicit = AgentApprovalStore(project, state_root=host_state)
    via_xdg = AgentApprovalStore(
        project,
        environ={"XDG_STATE_HOME": str(host_state)},
    )
    assert explicit.directory == via_xdg.directory
    assert explicit.directory.is_relative_to(host_state)
    assert explicit.project_root_hash.startswith("sha256:")
    assert len(explicit.project_root_hash) == 71
    assert not explicit.directory.is_relative_to(project)


def test_execution_approval_rejects_project_internal_or_relative_host_state(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(AgentApprovalError, match="cannot be inside the project"):
        AgentApprovalStore(project, state_root=project / ".approval-state")
    assert not (project / ".approval-state").exists()
    with pytest.raises(AgentApprovalError, match="must be absolute"):
        AgentApprovalStore(project, state_root=Path("relative-state"))


def test_execution_approval_rejects_symlinked_host_state_before_any_write(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AgentApprovalError, match="cannot contain symlinks"):
        AgentApprovalStore(project, state_root=linked_state)
    assert not (outside / "universal-research-mcp").exists()


def test_execution_approval_partitions_host_state_by_resolved_project_hash(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first-project"
    second = tmp_path / "second-project"
    first.mkdir()
    second.mkdir()
    host_state = tmp_path / "host-state"
    first_store = AgentApprovalStore(first, state_root=host_state)
    second_store = AgentApprovalStore(second, state_root=host_state)
    assert first_store.project_root_hash != second_store.project_root_hash
    assert first_store.directory != second_store.directory


def test_execution_approval_grant_cannot_cross_resolved_project_roots(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first-project"
    second = tmp_path / "second-project"
    first.mkdir()
    second.mkdir()
    host_state = tmp_path / "host-state"
    first_store = AgentApprovalStore(first, state_root=host_state)
    second_store = AgentApprovalStore(second, state_root=host_state)
    configuration = _Configuration()
    plan = _run_plan(configuration)
    _create_grant(first_store, plan, configuration)
    copied = json.loads(
        first_store.grant_path("approval_fixture").read_text(encoding="utf-8"),
    )
    second_store._create_json(second_store.grant_path("approval_fixture"), copied)
    with pytest.raises(AgentApprovalError, match="project_root_hash"):
        _consume_grant(
            second_store,
            plan,
            configuration,
            ({"authority": {"approval_refs": ["approval_fixture"]}},),
        )


def test_execution_approval_is_exact_create_only_and_consumed_once(tmp_path: Path) -> None:
    configuration = _Configuration()
    plan = _run_plan(configuration)
    store, project, host_state = _approval_store(tmp_path)
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    summary = _create_grant(store, plan, configuration, expires_at=expiry)
    assert summary["approval_ref"] == "approval_fixture"
    assert summary["run_plan_hash"] == plan["run_plan_hash"]
    assert summary["project_root_hash"] == store.project_root_hash
    assert summary["authority_source"] == "explicit_local_cli_approval"
    assert store.grant_path("approval_fixture").is_relative_to(host_state)
    assert not (project / "data/governance/approvals").exists()
    assert summary["credential_values_exposed"] is False
    with pytest.raises(AgentApprovalError, match="cannot be overwritten"):
        _create_grant(store, plan, configuration, expires_at=expiry)

    packets = ({"authority": {"approval_refs": ["approval_fixture"]}},)
    consumed = _consume_grant(store, plan, configuration, packets)
    assert consumed["approved"] is True
    assert consumed["project_root_hash"] == store.project_root_hash
    assert consumed["authority_source"] == "explicit_local_cli_approval"
    assert store.consumed_path("approval_fixture").is_file()
    consumption = json.loads(store.consumed_path("approval_fixture").read_text(encoding="utf-8"))
    assert consumption["project_root_hash"] == store.project_root_hash
    assert consumption["authority_source"] == "explicit_local_cli_approval"
    assert consumption["grant_hash"] == summary["grant_hash"]
    assert consumption["consumption_hash"] == hash_without(consumption, "consumption_hash")
    with pytest.raises(AgentApprovalError, match="already consumed"):
        _consume_grant(store, plan, configuration, packets)


def test_execution_approval_rejects_hash_mismatch_and_packet_self_approval(
    tmp_path: Path,
) -> None:
    configuration = _Configuration()
    plan = _run_plan(configuration)
    store, _project, _host_state = _approval_store(tmp_path)
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    with pytest.raises(AgentApprovalError, match="expected run plan hash"):
        _create_grant(
            store,
            plan,
            configuration,
            expected_run_plan_hash="sha256:not-the-plan",
            expires_at=expiry,
        )
    assert not store.grant_path("approval_fixture").exists()

    _create_grant(store, plan, configuration, expires_at=expiry)
    with pytest.raises(AgentApprovalError, match="task packet"):
        _consume_grant(
            store,
            plan,
            configuration,
            ({"authority": {"approval_refs": ["self_asserted_only"]}},),
        )
    assert not store.consumed_path("approval_fixture").exists()


def test_execution_approval_binds_estimate_snapshot_and_execution_request_hash(
    tmp_path: Path,
) -> None:
    configuration = _Configuration()
    plan = _run_plan(configuration)
    store, _project, _host_state = _approval_store(tmp_path)
    snapshot, snapshot_hash, request_hash = _approval_material(plan, configuration)
    with pytest.raises(AgentApprovalError, match="expected execution request hash"):
        store.create(
            plan,
            configuration,
            expected_run_plan_hash=plan["run_plan_hash"],
            expected_execution_request_hash="sha256:" + "0" * 64,
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            estimate_snapshot_hash=snapshot_hash,
            execution_request_hash=request_hash,
        )
    store.create(
        plan,
        configuration,
        expected_run_plan_hash=plan["run_plan_hash"],
        expected_execution_request_hash=request_hash,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        estimate_snapshot_hash=snapshot_hash,
        execution_request_hash=request_hash,
    )
    packets = ({"authority": {"approval_refs": ["approval_fixture"]}},)
    with pytest.raises(AgentApprovalError, match="estimate_snapshot_hash"):
        store.consume(
            plan,
            configuration,
            packets,
            {"unexpected": {"estimated_input_tokens": 1}},
            request_hash,
        )
    assert not store.consumed_path("approval_fixture").exists()
    with pytest.raises(AgentApprovalError, match="execution_request_hash"):
        store.consume(
            plan,
            configuration,
            packets,
            snapshot,
            artifact_hash({"different": True}),
        )
    assert not store.consumed_path("approval_fixture").exists()

    consumed = store.consume(
        plan,
        configuration,
        packets,
        snapshot,
        request_hash,
    )
    assert consumed["estimate_snapshot_hash"] == snapshot_hash
    assert consumed["execution_request_hash"] == request_hash


def test_execution_approval_rejects_symlink_and_oversized_grants(
    tmp_path: Path,
) -> None:
    configuration = _Configuration()
    plan = _run_plan(configuration)
    packets = ({"authority": {"approval_refs": ["approval_fixture"]}},)
    store, _project, _host_state = _approval_store(tmp_path)
    store.directory.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    store.grant_path("approval_fixture").symlink_to(outside)
    with pytest.raises(AgentApprovalError, match="unreadable"):
        _consume_grant(store, plan, configuration, packets)

    store.grant_path("approval_fixture").unlink()
    store.grant_path("approval_fixture").write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(AgentApprovalError, match="size"):
        _consume_grant(store, plan, configuration, packets)


def test_execution_approval_revalidates_directory_containment(
    tmp_path: Path,
) -> None:
    configuration = _Configuration()
    plan = _run_plan(configuration)
    store, _project, _host_state = _approval_store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    store.directory.parent.mkdir(parents=True)
    store.directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AgentApprovalError, match="unsafe"):
        _create_grant(store, plan, configuration)


@pytest.mark.parametrize("escape_component", ["application", "approvals"])
def test_execution_approval_never_creates_through_an_intermediate_symlink(
    tmp_path: Path,
    escape_component: str,
) -> None:
    configuration = _Configuration()
    plan = _run_plan(configuration)
    project = tmp_path / "project"
    project.mkdir()
    host_state = tmp_path / "host-state"
    store = AgentApprovalStore(project, state_root=host_state)
    outside = tmp_path / "outside"
    outside.mkdir()
    host_state.mkdir()
    if escape_component == "application":
        (host_state / "universal-research-mcp").symlink_to(outside, target_is_directory=True)
        escaped_creation = outside / "agent-approvals"
    else:
        (host_state / "universal-research-mcp").mkdir()
        (host_state / "universal-research-mcp/agent-approvals").symlink_to(
            outside, target_is_directory=True,
        )
        escaped_creation = outside / store.project_root_hash.removeprefix("sha256:")

    with pytest.raises(AgentApprovalError, match="unsafe"):
        _create_grant(store, plan, configuration)
    assert not escaped_creation.exists()


def test_execution_approval_write_all_handles_short_os_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import universal_research_mcp.runtime.agent_approval as approval_module

    configuration = _Configuration()
    plan = _run_plan(configuration)
    store, _project, _host_state = _approval_store(tmp_path)
    original_write = approval_module.os.write

    def short_write(descriptor: int, payload: object) -> int:
        return original_write(descriptor, bytes(payload)[:7])

    monkeypatch.setattr(approval_module.os, "write", short_write)
    _create_grant(store, plan, configuration)
    assert store._read_grant("approval_fixture")["run_plan_hash"] == plan["run_plan_hash"]


def test_real_agent_runtime_authorization_consumes_exact_grant_before_any_executor_call(
    tmp_path: Path,
) -> None:
    configuration = RunConfiguration(
        provider_id="openai-compatible-loopback",
        model="fixture-model",
        network_scope="loopback",
        provider_configuration_hash="sha256:" + "a" * 64,
        approval_ref="approval_fixture",
        max_workers=2,
        max_calls=2,
        max_input_tokens=10_000,
        max_output_tokens=1_000,
        max_output_tokens_per_agent=500,
        max_cost_usd=0.0,
        timeout_seconds=30.0,
    )
    plan = _run_plan(configuration)
    packets = ({"authority": {"approval_refs": ["approval_fixture"]}},)
    store, project, _host_state = _approval_store(tmp_path)
    snapshot, snapshot_hash, request_hash = _approval_material(plan, configuration)
    _create_grant(store, plan, configuration)
    executor_calls: list[dict[str, Any]] = []
    runtime = AgentRuntime(
        project,
        lambda dispatch: executor_calls.append(dispatch),
        approval_validator=store.consume,
    )
    prepared = SimpleNamespace(
        run_plan=plan,
        run_plan_hash=plan["run_plan_hash"],
        configuration=configuration,
        packets=packets,
        estimate_snapshot=snapshot,
        estimate_snapshot_hash=snapshot_hash,
        execution_request_hash=request_hash,
    )
    authorization, issues = runtime._authorize(prepared)
    assert issues == []
    assert authorization is not None
    assert authorization["configuration_hash"] == artifact_hash(configuration.to_dict())
    assert executor_calls == []
    assert store.consumed_path("approval_fixture").is_file()

    second, second_issues = runtime._authorize(prepared)
    assert second is None
    assert second_issues[0]["code"] == "RUNTIME-APPROVAL"
    assert "already consumed" in second_issues[0]["message"]
    assert executor_calls == []
