from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time

import pytest

from governance.hashing import artifact_hash, hash_without
from governance.registry import CRITICAL, load_registry, manifest_hash
from governance.scope_policy import task_scope_hash
from universal_research_mcp.agent_runtime import RuntimeDispatchReservationAuthority
from universal_research_mcp.harness import ParallelResearchHarness
from universal_research_mcp.harness import (
    AppendOnlyJsonlSink,
    ProviderAgentExecutor,
    ProviderOutputError,
)
from universal_research_mcp.providers import (
    BudgetExceeded,
    CredentialResolver,
    HttpResponse,
    OpenAIProvider,
    ProviderRouter,
    RemoteBudget,
    RemotePolicy,
)
from integrations.codex.adapter import build_dispatch_request


def packet(
    agent_id: str,
    *,
    mode: str = "benchmark",
    workflow_id: str = "workflow_harness",
    snapshot: str = "sha256:snapshot",
    max_parallelism: int = 2,
    max_cost: float = 1.0,
) -> dict:
    manifest = load_registry()[agent_id]
    scope = {
        "allowed_paths": ["data/events"],
        "allowed_sources": ["canonical"],
        "allowed_actions": [manifest["authority"]["allowed_actions"][0]],
        "forbidden_actions": manifest["authority"]["forbidden_actions"],
        "allowed_capabilities": [],
        "allowed_providers": [],
        "allow_network": False,
        "allow_model_execution": False,
        "allow_benchmark": mode != "lightweight",
        "allow_background": False,
        "max_parallelism": max_parallelism,
        "estimated_cost_usd": 0.0,
        "max_cost_usd": max_cost,
    }
    evidence = {
        "record_ids": ["record_fixture"], "result_ids": [],
        "dataset_hashes": [], "model_hashes": [], "artifact_revisions": [],
        "commit_ids": [], "snapshot_hash": snapshot,
    }
    value = {
        "schema_version": "research-agent-task/1.0",
        "governance_version": "agent-governance/2.0",
        "run_id": "run_harness",
        "workflow_id": workflow_id,
        "agent_id": agent_id,
        "requester": {"type": "user", "id": "actor_user"},
        "purpose": "Run a bounded harness fixture.",
        "mode": mode,
        "scope": scope,
        "evidence_boundary": evidence,
        "authority": {
            "approval_refs": [], "authority_basis": "test approval",
            "scope_hash": "pending", "plan_refs": ["plan_fixture"],
            "user_opt_ins": [],
        },
        "failure_policy": {
            "stop": "blocking_only", "record": "ask", "detail": "redacted",
        },
        "success_criteria": ["Return one validated decision."],
        "stop_conditions": ["Invalid output."],
        "role_manifest_hash": manifest_hash(manifest),
        "created_at": "2026-08-04T00:00:00+00:00",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    value["authority"]["scope_hash"] = task_scope_hash(value)
    return value


def decision(task: dict, *, status: str = "pass") -> dict:
    classification = {"analysis_type": "descriptive", "claim_eligibility": "eligible"}
    decisions = []
    if task["agent_id"] == "scope_and_cost_governor":
        classification = {
            "reviewed_plan_hash": artifact_hash({"task": artifact_hash(task)}),
            "necessity_verdict": "required",
            "difficulty": "low",
            "estimate_confidence": "high",
            "scope_verdict": "within_approved_scope",
            "additional_work": "optional",
        }
        decisions = [{
            "elapsed_time_range": {"minimum": "1m", "likely": "2m", "maximum": "5m"},
            "work_units": {"agent_calls": 1},
            "resource_cost": {"paid_api_usage_usd": 0},
            "assumptions": ["Fixture executor is deterministic."],
            "evidence_refs": [{"task_packet_hash": artifact_hash(task)}],
            "user_choice_required": False,
        }]
    value = {
        "schema_version": "research-agent-decision/1.0",
        "run_id": task["run_id"], "workflow_id": task["workflow_id"],
        "agent_id": task["agent_id"],
        "role_manifest_hash": task["role_manifest_hash"],
        "task_packet_hash": artifact_hash(task),
        "status": status, "summary": f"{task['agent_id']} completed.",
        "classification": classification,
        "findings": [], "evidence": [], "commands": [], "decisions": decisions,
        "recommended_actions": [],
        "authority_used": task["scope"]["allowed_actions"],
        "limitations": [],
        "attribution": {
            "requester": "user", "proposer": "user",
            "executor": task["agent_id"], "reviewer": "",
        },
        "started_at": "2026-08-04T00:00:00+00:00",
        "completed_at": "2026-08-04T00:01:00+00:00",
    }
    value["output_hash"] = hash_without(value, "output_hash")
    return value


def task_map(packets: list[dict]) -> dict[str, dict]:
    return {task["agent_id"]: task for task in packets}


def test_governor_runs_before_workers_and_independent_workers_are_parallel() -> None:
    packets = [
        packet("retrieval_governor"),
        packet("scope_and_cost_governor", max_parallelism=1),
        packet("analysis_objectivity_auditor"),
    ]
    tasks = task_map(packets)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    calls: list[str] = []
    active = 0
    maximum_active = 0

    def executor(dispatch: dict) -> dict:
        nonlocal active, maximum_active
        agent_id = dispatch["agent_id"]
        with lock:
            calls.append(agent_id)
        if agent_id != "scope_and_cost_governor":
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            barrier.wait(timeout=2)
            time.sleep(0.01)
            with lock:
                active -= 1
        return decision(tasks[agent_id])

    records: list[dict] = []
    result = ParallelResearchHarness(executor, lambda record: not records.append(record)).run(
        packets,
        max_workers=2,
        aggregate_cost_ceiling_usd=1.0,
        declared_costs_usd={
            "retrieval_governor": 0.1,
            "analysis_objectivity_auditor": 0.1,
            "scope_and_cost_governor": 0.0,
        },
    )

    assert result["status"] == "completed"
    assert result["claim_eligibility"] == "eligible"
    assert calls[0] == "scope_and_cost_governor"
    assert maximum_active == 2
    assert [item["agent_id"] for item in result["results"]] == [
        "retrieval_governor", "analysis_objectivity_auditor",
    ]
    assert len(records) == 4
    assert any(record.get("record_type") == "scope_governor_receipt" for record in records)


def test_budget_rejection_happens_before_any_executor_call() -> None:
    packets = [packet("scope_and_cost_governor", max_parallelism=1), packet("retrieval_governor")]
    calls: list[str] = []
    result = ParallelResearchHarness(lambda dispatch: calls.append(dispatch["agent_id"])).run(
        packets,
        max_workers=2,
        aggregate_cost_ceiling_usd=0.1,
        declared_costs_usd={
            "scope_and_cost_governor": 0.0,
            "retrieval_governor": 0.2,
        },
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "preflight_rejected"
    assert calls == []


def test_packet_cost_estimate_is_mandatory_when_no_explicit_map_is_supplied() -> None:
    governor = packet("scope_and_cost_governor", max_parallelism=1)
    worker = packet("retrieval_governor")
    worker["scope"]["estimated_cost_usd"] = 0.2
    worker["authority"]["scope_hash"] = task_scope_hash(worker)
    calls: list[str] = []

    result = ParallelResearchHarness(
        lambda dispatch: calls.append(dispatch["agent_id"]),
    ).run([governor, worker], max_workers=1, aggregate_cost_ceiling_usd=0.1)

    assert result["reason"] == "preflight_rejected"
    assert calls == []


def test_first_execution_failure_stops_new_submission_without_retry_or_force_kill() -> None:
    packets = [
        packet("scope_and_cost_governor", max_parallelism=1),
        packet("retrieval_governor", max_parallelism=1),
        packet("analysis_objectivity_auditor", max_parallelism=1),
    ]
    tasks = task_map(packets)
    calls: list[str] = []

    def executor(dispatch: dict) -> dict:
        agent_id = dispatch["agent_id"]
        calls.append(agent_id)
        if agent_id == "retrieval_governor":
            raise RuntimeError("fixture provider failure")
        return decision(tasks[agent_id])

    result = ParallelResearchHarness(executor).run(
        packets, max_workers=1, aggregate_cost_ceiling_usd=0,
    )
    assert result["status"] == "blocked"
    assert calls == ["scope_and_cost_governor", "retrieval_governor"]
    assert calls.count("retrieval_governor") == 1
    assert result["hidden_retries"] == 0
    assert result["force_killed_calls"] == 0
    assert result["failures"][0]["canonical_minimum_record_preserved"] is True
    assert result["claim_eligibility"] == "blocked"


def test_harness_blocks_contradictory_passing_governor_before_worker() -> None:
    packets = [
        packet("scope_and_cost_governor", max_parallelism=1),
        packet("retrieval_governor", max_parallelism=1),
    ]
    tasks = task_map(packets)
    calls: list[str] = []

    def executor(dispatch: dict) -> dict:
        agent_id = dispatch["agent_id"]
        calls.append(agent_id)
        value = decision(tasks[agent_id])
        if agent_id == "scope_and_cost_governor":
            value["classification"]["scope_verdict"] = "reapproval_required"
            value["output_hash"] = hash_without(value, "output_hash")
        return value

    result = ParallelResearchHarness(executor).run(
        packets, max_workers=1, aggregate_cost_ceiling_usd=0,
    )

    assert result["status"] == "blocked"
    assert calls == ["scope_and_cost_governor"]


def test_critical_batch_requires_exactly_four_same_snapshot_reviewers() -> None:
    governor = packet(
        "scope_and_cost_governor", mode="final_review", max_parallelism=1,
    )
    incomplete = [governor, *[
        packet(agent_id, mode="final_review", max_parallelism=4)
        for agent_id in sorted(CRITICAL)[:-1]
    ]]
    calls: list[str] = []
    blocked = ParallelResearchHarness(lambda dispatch: calls.append(dispatch["agent_id"])).run(
        incomplete, max_workers=4, aggregate_cost_ceiling_usd=0,
    )
    assert blocked["reason"] == "preflight_rejected"
    assert calls == []

    complete = [governor, *[
        packet(agent_id, mode="final_review", max_parallelism=4)
        for agent_id in sorted(CRITICAL)
    ]]
    tasks = task_map(complete)
    result = ParallelResearchHarness(
        lambda dispatch: decision(tasks[dispatch["agent_id"]]),
        lambda _record: True,
    ).run(complete, max_workers=4, aggregate_cost_ceiling_usd=0)
    assert result["status"] == "completed"
    assert {item["agent_id"] for item in result["results"]} == set(CRITICAL)


class _Transport:
    def __init__(self, text: str, *, response_model: str = "generation-fixture") -> None:
        self.text = text
        self.response_model = response_model
        self.calls = 0

    def request(self, **_kwargs) -> HttpResponse:
        self.calls += 1
        return HttpResponse(200, {
            "model": self.response_model,
            "choices": [{"message": {"content": self.text}}],
        })


def _runtime_dispatch(
    executor: ProviderAgentExecutor,
    dispatch: dict,
    *,
    provider_id: str = "openai",
    network_scope: str = "remote",
) -> dict:
    provider_hash = "sha256:" + "a" * 64
    executor.provider_id = provider_id
    executor.network_scope = network_scope
    executor.provider_configuration_hash = provider_hash
    parent_hash = dispatch.pop("dispatch_hash")
    dispatch["schema_version"] = "urag-runtime-dispatch/1.0"
    dispatch["parent_dispatch_hash"] = parent_hash
    dispatch.update({
        "run_plan_hash": "sha256:" + "1" * 64,
        "estimate_snapshot_hash": "sha256:" + "2" * 64,
        "execution_request_hash": "sha256:" + "3" * 64,
        "evidence_bundle": {"passages": []},
        "evidence_bundle_hash": "sha256:" + "4" * 64,
        "provider_configuration_hash": provider_hash,
    })
    runtime_binding = {
        "session_id": "session_fixture",
        "run_plan_hash": dispatch["run_plan_hash"],
        "estimate_snapshot_hash": dispatch["estimate_snapshot_hash"],
        "execution_request_hash": dispatch["execution_request_hash"],
        "scope_governor_receipt_hash": dispatch["scope_governor_receipt_hash"],
        "provider_configuration_hash": provider_hash,
    }
    dispatch["role_instructions"]["runtime_binding"] = runtime_binding
    dispatch["runtime"] = {
        **runtime_binding,
        "prompt_hash": "sha256:" + "5" * 64,
        "prompt_pack_hash": dispatch["role_prompt_hash"],
        "evidence_bundle_hash": dispatch["evidence_bundle_hash"],
        "provider_id": provider_id,
        "model": executor.model,
        "network_scope": network_scope,
        "timeout_seconds": executor.request_timeout_seconds,
        "configuration_hash": "sha256:" + "6" * 64,
        "parent_dispatch_hash": parent_hash,
    }
    dispatch["runtime_dispatch_hash"] = hash_without(
        dispatch, "runtime_dispatch_hash",
    )
    return dispatch


def _bind_and_reserve(
    executor: ProviderAgentExecutor,
    dispatch: dict,
) -> RuntimeDispatchReservationAuthority:
    authority = RuntimeDispatchReservationAuthority()
    executor.bind_runtime_dispatch_consumer(authority.consumer())
    authority.reserve(artifact_hash(dispatch))
    return authority


def test_provider_agent_executor_strict_json_and_aggregate_budget(tmp_path: Path) -> None:
    body = json.dumps({
        "status": "pass", "summary": "Bounded review complete.",
        "classification": {}, "findings": [], "evidence": [],
        "decisions": [], "recommended_actions": [], "authority_used": [],
        "limitations": [],
    })
    transport = _Transport(body, response_model="generation-fixture")
    provider = OpenAIProvider(
        transport=transport, credential_ref="env:OPENAI_API_KEY",
    )
    policy = RemotePolicy(
        approved=True,
        allowed_provider_ids=frozenset({"openai"}),
        budget=RemoteBudget(1, 100_000, 10_000, 1_000_000),
    )
    executor = ProviderAgentExecutor(
        router=ProviderRouter(
            local=None, remotes=(provider,),
            credentials=CredentialResolver(environ={"OPENAI_API_KEY": "sk-fixture"}),
        ),
        remote_policy=policy,
        model="generation-fixture",
        max_output_tokens=100,
        input_cost_per_million_tokens_usd="1",
        output_cost_per_million_tokens_usd="1",
    )
    task = packet("scope_and_cost_governor", max_parallelism=1)
    dispatch = _runtime_dispatch(executor, build_dispatch_request(task))
    with pytest.raises(ProviderOutputError, match="unused host reservation"):
        executor(dispatch)
    assert transport.calls == 0
    authority = _bind_and_reserve(executor, dispatch)
    result = executor(dispatch)
    assert result["status"] == "pass"
    assert result["attribution"]["executor"] == "openai:generation-fixture"
    assert result["attribution"]["provider_reported_model"] == "generation-fixture"
    assert transport.calls == 1
    with pytest.raises(ProviderOutputError, match="unused host reservation"):
        executor(dispatch)
    assert transport.calls == 1
    second_dispatch = json.loads(json.dumps(dispatch))
    second_dispatch["run_id"] = "run-harness-budget-2"
    second_dispatch["runtime_dispatch_hash"] = hash_without(
        second_dispatch, "runtime_dispatch_hash",
    )
    authority.reserve(artifact_hash(second_dispatch))
    with pytest.raises(BudgetExceeded):
        executor(second_dispatch)
    assert transport.calls == 1

    sink = AppendOnlyJsonlSink(tmp_path)
    assert sink({"record_id": "first", "status": "recorded"}) is True
    assert json.loads((tmp_path / "data/governance/harness.jsonl").read_text().strip())["record_id"] == "first"


def test_provider_agent_executor_rejects_response_model_alias_without_retry() -> None:
    body = json.dumps({
        "status": "pass", "summary": "Bounded review complete.",
        "classification": {}, "findings": [], "evidence": [],
        "decisions": [], "recommended_actions": [], "authority_used": [],
        "limitations": [],
    })
    transport = _Transport(body, response_model="generation-fixture-resolved")
    provider = OpenAIProvider(
        transport=transport, credential_ref="env:OPENAI_API_KEY",
    )
    executor = ProviderAgentExecutor(
        router=ProviderRouter(
            local=None, remotes=(provider,),
            credentials=CredentialResolver(environ={"OPENAI_API_KEY": "sk-fixture"}),
        ),
        remote_policy=RemotePolicy(
            approved=True,
            allowed_provider_ids=frozenset({"openai"}),
            budget=RemoteBudget(2, 100_000, 10_000, 1_000_000),
        ),
        model="generation-fixture",
        max_output_tokens=100,
        input_cost_per_million_tokens_usd="1",
        output_cost_per_million_tokens_usd="1",
    )

    dispatch = _runtime_dispatch(
        executor,
        build_dispatch_request(packet("scope_and_cost_governor", max_parallelism=1)),
    )
    _bind_and_reserve(executor, dispatch)
    with pytest.raises(ProviderOutputError, match="pinned model"):
        executor(dispatch)
    assert transport.calls == 1


def test_provider_agent_executor_rejects_non_json_without_repair() -> None:
    transport = _Transport("not-json")
    provider = OpenAIProvider(
        transport=transport, credential_ref="env:OPENAI_API_KEY",
    )
    policy = RemotePolicy(
        approved=True,
        allowed_provider_ids=frozenset({"openai"}),
        budget=RemoteBudget(1, 100_000, 10_000, 1_000_000),
    )
    executor = ProviderAgentExecutor(
        router=ProviderRouter(
            local=None, remotes=(provider,),
            credentials=CredentialResolver(environ={"OPENAI_API_KEY": "sk-fixture"}),
        ),
        remote_policy=policy,
        model="generation-fixture",
        max_output_tokens=100,
        input_cost_per_million_tokens_usd="1",
        output_cost_per_million_tokens_usd="1",
    )
    dispatch = _runtime_dispatch(
        executor,
        build_dispatch_request(packet("scope_and_cost_governor", max_parallelism=1)),
    )
    _bind_and_reserve(executor, dispatch)
    with pytest.raises(ProviderOutputError):
        executor(dispatch)
    assert transport.calls == 1
