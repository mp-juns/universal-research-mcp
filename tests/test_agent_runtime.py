from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from typing import Any

import pytest

from governance.hashing import artifact_hash, hash_without
from governance.registry import load_registry, manifest_hash
from governance.scope_policy import task_scope_hash
from universal_research_mcp.agent_runtime import (
    AgentRuntime,
    EvidenceBundle,
    EvidencePassage,
    ProjectEvidenceBundleBuilder,
    RunConfiguration,
    RuntimeStoreError,
)
from universal_research_mcp.agent_runtime.evidence import evidence_snapshot_hash


PROVIDER_CONFIGURATION_HASH = artifact_hash({"provider": "fixture", "revision": 1})


def _passage(record_id: str = "record_fixture") -> EvidencePassage:
    content = "Bounded fixture evidence."
    digest = hashlib.sha256((content + "\n").encode()).hexdigest()
    return EvidencePassage(
        record_id=record_id,
        source_id="source_fixture",
        path="docs/evidence.md",
        source_sha256=digest,
        line_start=1,
        line_end=1,
        content=content,
    )


def _boundary(record_id: str = "record_fixture") -> tuple[dict[str, Any], EvidenceBundle]:
    material: dict[str, Any] = {
        "record_ids": [record_id],
        "result_ids": [],
        "dataset_hashes": [],
        "model_hashes": [],
        "artifact_revisions": [],
        "commit_ids": [],
    }
    boundary_hash = artifact_hash(material)
    passages = (_passage(record_id),)
    snapshot_hash = evidence_snapshot_hash(boundary_hash, passages)
    boundary = {**material, "snapshot_hash": snapshot_hash}
    return boundary, EvidenceBundle(snapshot_hash, boundary_hash, passages)


class _EvidenceBuilder:
    def build(self, packet: dict[str, Any], _root: Path) -> EvidenceBundle:
        _, bundle = _boundary(packet["evidence_boundary"]["record_ids"][0])
        return bundle


def _packet(agent_id: str, *, run_id: str = "run_fixture") -> dict[str, Any]:
    registry = load_registry()
    manifest = registry[agent_id]
    boundary, _ = _boundary()
    scope = {
        "allowed_paths": ["docs/**"],
        "allowed_sources": ["canonical"],
        "allowed_actions": [manifest["authority"]["allowed_actions"][0]],
        "forbidden_actions": manifest["authority"]["forbidden_actions"],
        "allowed_capabilities": [],
        "allowed_providers": ["fixture-provider"],
        "allow_network": False,
        "allow_model_execution": True,
        "allow_benchmark": False,
        "allow_background": False,
        "max_parallelism": 2,
        "estimated_cost_usd": 0.0,
        "max_cost_usd": 0.0,
    }
    packet = {
        "schema_version": "research-agent-task/1.0",
        "governance_version": "agent-governance/2.0",
        "run_id": run_id,
        "workflow_id": "workflow_fixture",
        "agent_id": agent_id,
        "requester": {"type": "user", "id": "actor_fixture"},
        "purpose": "Perform one bounded runtime fixture review.",
        "mode": "lightweight",
        "scope": scope,
        "evidence_boundary": boundary,
        "authority": {
            "approval_refs": ["approval_fixture"],
            "authority_basis": "explicit fixture approval",
            "scope_hash": "pending",
            "plan_refs": ["plan_fixture"],
            "user_opt_ins": [],
        },
        "failure_policy": {
            "stop": "blocking_only",
            "record": "ask",
            "detail": "redacted",
        },
        "success_criteria": ["Return one hash-bound decision."],
        "stop_conditions": ["Any hash or scope mismatch."],
        "role_manifest_hash": manifest_hash(manifest),
        "created_at": "2026-08-04T00:00:00+00:00",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    packet["authority"]["scope_hash"] = task_scope_hash(packet)
    return packet


def _configuration(**overrides: Any) -> RunConfiguration:
    values: dict[str, Any] = {
        "provider_id": "fixture-provider",
        "model": "fixture-model",
        "network_scope": "none",
        "provider_configuration_hash": PROVIDER_CONFIGURATION_HASH,
        "approval_ref": "approval_fixture",
        "max_workers": 2,
        "max_calls": 3,
        "max_input_tokens": 20_000,
        "max_output_tokens": 1_000,
        "max_output_tokens_per_agent": 400,
        "max_cost_usd": 0.0,
        "timeout_seconds": 10.0,
    }
    values.update(overrides)
    return RunConfiguration(**values)


def _approval(
    plan: dict[str, Any], config: RunConfiguration,
    _packets: tuple[dict[str, Any], ...], estimate_snapshot: dict[str, Any],
    execution_request_hash: str,
) -> dict[str, Any]:
    return {
        "approved": True,
        "grant_id": "grant_fixture",
        "run_plan_hash": plan["run_plan_hash"],
        "estimate_snapshot_hash": artifact_hash(estimate_snapshot),
        "execution_request_hash": execution_request_hash,
        "provider_id": config.provider_id,
        "model": config.model,
        "provider_configuration_hash": config.provider_configuration_hash,
        "configuration_hash": artifact_hash(config.to_dict()),
        "approval_ref": config.approval_ref,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "grant_hash": artifact_hash({"grant": "fixture"}),
        "consumption_hash": artifact_hash({"consumption": "fixture"}),
        "authority_source": "fixture_user_approval",
    }


class _Executor:
    provider_id = "fixture-provider"
    model = "fixture-model"
    network_scope = "none"
    provider_configuration_hash = PROVIDER_CONFIGURATION_HASH
    request_timeout_seconds = 10.0

    def __init__(
        self,
        *,
        bad_agent: str | None = None,
        bad_kind: str | None = None,
        parallel_workers: int = 0,
        mutate_runtime_dispatch: bool = False,
    ) -> None:
        self.bad_agent = bad_agent
        self.bad_kind = bad_kind
        self.calls: list[str] = []
        self.dispatches: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._active = 0
        self.maximum_active = 0
        self._barrier = threading.Barrier(parallel_workers) if parallel_workers else None
        self.mutate_runtime_dispatch = mutate_runtime_dispatch

    @staticmethod
    def estimate_dispatch(_dispatch: dict[str, Any]) -> dict[str, int]:
        return {
            "estimated_input_tokens": 100,
            "max_output_tokens": 50,
            "estimated_cost_micros": 0,
        }

    def __call__(self, dispatch: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(dispatch["agent_id"])
        with self._lock:
            self.calls.append(agent_id)
            self.dispatches.append(dispatch)
        if agent_id != "scope_and_cost_governor" and self._barrier is not None:
            with self._lock:
                self._active += 1
                self.maximum_active = max(self.maximum_active, self._active)
            self._barrier.wait(timeout=2)
            time.sleep(0.01)
            with self._lock:
                self._active -= 1
        classification: dict[str, Any] = {
            "prompt_pack_hash": dispatch["role_prompt_hash"],
            "evidence_bundle_hash": dispatch["evidence_bundle_hash"],
        }
        if agent_id == "scope_and_cost_governor":
            classification.update({
                "reviewed_plan_hash": dispatch["run_plan_hash"],
                "necessity_verdict": "required",
                "difficulty": "low",
                "estimate_confidence": "high",
                "scope_verdict": "within_approved_scope",
                "additional_work": "optional",
            })
        if agent_id == "analysis_objectivity_auditor":
            classification.update({
                "analysis_type": "descriptive",
                "claim_eligibility": "eligible",
            })
        if self.bad_agent == agent_id and self.bad_kind == "prompt_hash":
            classification["prompt_pack_hash"] = "sha256:" + "0" * 64
        evidence: list[Any] = []
        if agent_id != "scope_and_cost_governor":
            evidence = [dispatch["evidence_bundle"]["passages"][0]["evidence_ref"]]
        if self.bad_agent == agent_id and self.bad_kind == "evidence_ref":
            evidence = ["source:outside|path:private.txt|sha256:" + "0" * 64 + "|lines:1-1"]
        if self.bad_agent == agent_id and self.bad_kind == "missing_citation":
            evidence = []
        now = datetime.now(timezone.utc).isoformat()
        decisions: list[dict[str, Any]] = []
        if agent_id == "scope_and_cost_governor":
            decisions.append({
                "elapsed_time_range": {"minimum": "1m", "likely": "2m", "maximum": "5m"},
                "work_units": {"agent_calls": len(dispatch["role_instructions"]["exact_run_plan_under_review"]["tasks"])},
                "resource_cost": {"paid_api_usage_usd": 0.0},
                "assumptions": ["Fixture executor is deterministic."],
                "evidence_refs": [{"run_plan_hash": dispatch["run_plan_hash"]}],
                "user_choice_required": False,
            })
        decision = {
            "schema_version": "research-agent-decision/1.0",
            "run_id": dispatch["run_id"],
            "workflow_id": dispatch["workflow_id"],
            "agent_id": agent_id,
            "role_manifest_hash": dispatch["role_manifest_hash"],
            "task_packet_hash": dispatch["task_packet_hash"],
            "status": "pass",
            "summary": f"{agent_id} completed its bounded review.",
            "classification": classification,
            "findings": [],
            "evidence": evidence,
            "commands": [],
            "decisions": decisions,
            "recommended_actions": [],
            "authority_used": [],
            "limitations": [],
            "attribution": {
                "requester": "user",
                "proposer": "central_manager",
                "executor": "fixture-provider:fixture-model",
                "reviewer": agent_id,
            },
            "started_at": now,
            "completed_at": now,
        }
        decision["output_hash"] = hash_without(decision, "output_hash")
        if self.mutate_runtime_dispatch:
            dispatch["runtime"]["model"] = "mutated-by-executor"
        return decision


class _ReservationAwareExecutor(_Executor):
    def __init__(self) -> None:
        super().__init__()
        self._reservation_consumer: Any = None

    def bind_runtime_dispatch_consumer(self, consumer: Any) -> None:
        assert self._reservation_consumer is None
        self._reservation_consumer = consumer

    def __call__(self, dispatch: dict[str, Any]) -> dict[str, Any]:
        if (
            self._reservation_consumer is None
            or not self._reservation_consumer.consume(artifact_hash(dispatch))
        ):
            raise RuntimeError("missing single-use runtime dispatch reservation")
        return super().__call__(dispatch)


def _runtime(root: Path, executor: _Executor, approval_validator=_approval) -> AgentRuntime:
    return AgentRuntime(
        root,
        executor,
        evidence_builder=_EvidenceBuilder(),
        approval_validator=approval_validator,
    )


def test_governor_first_independent_sessions_are_actually_parallel_and_hash_bound(
    tmp_path: Path,
) -> None:
    executor = _Executor(parallel_workers=2)
    runtime = _runtime(tmp_path, executor)
    packets = [
        _packet("retrieval_governor"),
        _packet("scope_and_cost_governor"),
        _packet("analysis_objectivity_auditor"),
    ]

    result = runtime.run(packets, _configuration())

    assert result["status"] == "completed"
    assert result["claim_eligibility"] == "eligible"
    assert result["hidden_retries"] == 0
    assert executor.calls[0] == "scope_and_cost_governor"
    assert executor.maximum_active == 2
    assert len(executor.calls) == 3
    assert result["estimate_snapshot_hash"].startswith("sha256:")
    assert result["execution_request_hash"].startswith("sha256:")
    assert len({dispatch["runtime"]["session_id"] for dispatch in executor.dispatches}) == 3
    for dispatch in executor.dispatches:
        assert dispatch["role_prompt_hash"] in dispatch["role_prompt"]
        assert dispatch["run_plan_hash"] == result["run_plan_hash"]
        assert dispatch["estimate_snapshot_hash"] == result["estimate_snapshot_hash"]
        assert dispatch["execution_request_hash"] == result["execution_request_hash"]
        assert dispatch["evidence_bundle_hash"] == dispatch["evidence_bundle"]["bundle_hash"]
        assert "Bounded fixture evidence." not in json.dumps(dispatch["role_instructions"])
        assert dispatch["evidence_bundle"]["passages"][0]["content"] == "Bounded fixture evidence."
    inspection = runtime.inspect("run_fixture")
    assert inspection["status"]["state"] == "completed"
    assert len(inspection["sessions"]) == 3
    assert all(
        session["decision"]["controller_summary"]
        == "Validated decision is available in the internal session artifact."
        for session in inspection["sessions"]
    )
    assert "completed its bounded review" not in json.dumps(inspection)
    assert (tmp_path / "data/governance/runs/run_fixture/run-seal.json").is_file()


def test_executor_cannot_mutate_the_stored_runtime_dispatch_or_decision_binding(
    tmp_path: Path,
) -> None:
    executor = _Executor(mutate_runtime_dispatch=True)
    runtime = _runtime(tmp_path, executor)

    result = runtime.run(
        [_packet("scope_and_cost_governor"), _packet("retrieval_governor")],
        _configuration(max_calls=2, max_workers=1),
    )

    assert result["status"] == "completed"
    for captured in executor.dispatches:
        assert captured["runtime"]["model"] == "mutated-by-executor"
        session_id = captured["runtime"]["session_id"]
        session_root = (
            tmp_path / "data/governance/runs/run_fixture/sessions" / session_id
        )
        stored_dispatch = json.loads(
            (session_root / "dispatch.json").read_text(encoding="utf-8")
        )
        decision_envelope = json.loads(
            (session_root / "decision.json").read_text(encoding="utf-8")
        )
        assert stored_dispatch["runtime"]["model"] == "fixture-model"
        assert stored_dispatch["runtime_dispatch_hash"] == hash_without(
            stored_dispatch, "runtime_dispatch_hash",
        )
        assert decision_envelope["dispatch_hash"] == artifact_hash(stored_dispatch)


def test_runtime_reserves_each_provider_dispatch_once_and_replay_is_rejected(
    tmp_path: Path,
) -> None:
    executor = _ReservationAwareExecutor()
    runtime = _runtime(tmp_path, executor)

    result = runtime.run(
        [_packet("scope_and_cost_governor"), _packet("retrieval_governor")],
        _configuration(max_calls=2, max_workers=1),
    )

    assert result["status"] == "completed"
    assert len(executor.dispatches) == 2
    with pytest.raises(RuntimeError, match="single-use"):
        executor(executor.dispatches[0])


def test_run_requires_explicit_exact_approval_and_does_not_call_executor(tmp_path: Path) -> None:
    executor = _Executor()
    runtime = _runtime(tmp_path, executor, approval_validator=None)

    result = runtime.run(
        [_packet("scope_and_cost_governor"), _packet("retrieval_governor")],
        _configuration(max_calls=2),
    )

    assert result["reason"] == "execution_approval_rejected"
    assert result["executed"] is False
    assert executor.calls == []
    assert not (tmp_path / "data/governance/runs/run_fixture").exists()


def test_network_none_cannot_disguise_a_remote_provider(tmp_path: Path) -> None:
    executor = _Executor()
    executor.router = SimpleNamespace(
        local=None,
        remotes=(SimpleNamespace(provider_id="fixture-provider", is_remote=True),),
    )
    runtime = _runtime(tmp_path, executor)

    report = runtime.preflight(
        [_packet("scope_and_cost_governor"), _packet("retrieval_governor")],
        _configuration(max_calls=2),
    )

    assert report["valid"] is False
    assert any("topology" in issue["message"] for issue in report["issues"])
    assert executor.calls == []


def test_execution_approval_must_bind_exact_estimate_and_request_hash(
    tmp_path: Path,
) -> None:
    executor = _Executor()

    def mismatched_approval(
        plan: dict[str, Any], config: RunConfiguration,
        packets: tuple[dict[str, Any], ...], estimate_snapshot: dict[str, Any],
        execution_request_hash: str,
    ) -> dict[str, Any]:
        grant = _approval(
            plan, config, packets, estimate_snapshot, execution_request_hash,
        )
        grant["estimate_snapshot_hash"] = artifact_hash({"tampered": True})
        return grant

    runtime = _runtime(tmp_path, executor, approval_validator=mismatched_approval)
    result = runtime.run(
        [_packet("scope_and_cost_governor"), _packet("retrieval_governor")],
        _configuration(max_calls=2, max_workers=1),
    )

    assert result["reason"] == "execution_approval_rejected"
    assert result["executed"] is False
    assert executor.calls == []


def test_source_required_pass_without_exact_bundle_citation_is_blocked(
    tmp_path: Path,
) -> None:
    executor = _Executor(
        bad_agent="retrieval_governor", bad_kind="missing_citation",
    )
    runtime = _runtime(tmp_path, executor)

    result = runtime.run(
        [_packet("scope_and_cost_governor"), _packet("retrieval_governor")],
        _configuration(max_calls=2, max_workers=1),
    )

    assert result["status"] == "blocked"
    assert result["failure_count"] == 1
    assert executor.calls == ["scope_and_cost_governor", "retrieval_governor"]


@pytest.mark.parametrize("bad_kind", ["prompt_hash", "evidence_ref"])
def test_invalid_hash_or_out_of_bundle_evidence_stops_once_and_preserves_tombstone(
    tmp_path: Path, bad_kind: str,
) -> None:
    executor = _Executor(bad_agent="retrieval_governor", bad_kind=bad_kind)
    runtime = _runtime(tmp_path, executor)

    result = runtime.run(
        [_packet("scope_and_cost_governor"), _packet("retrieval_governor")],
        _configuration(max_calls=2, max_workers=1),
    )

    assert result["status"] == "blocked"
    assert result["hidden_retries"] == 0
    assert executor.calls == ["scope_and_cost_governor", "retrieval_governor"]
    assert result["failure_count"] == 1
    assert result["user_choice_required"] is True
    assert len(result["pending_failure_record_choices"]) == 1
    inspection = runtime.inspect("run_fixture", "retrieval_governor")
    assert inspection["sessions"][0]["decision"] is None
    assert {"failure.json", "raw-output.json"} <= set(
        inspection["sessions"][0]["artifact_names"]
    )
    session_id = inspection["sessions"][0]["session_id"]
    raw = json.loads(
        (tmp_path / f"data/governance/runs/run_fixture/sessions/{session_id}/raw-output.json")
        .read_text(encoding="utf-8")
    )
    assert raw["detail_omitted_by_policy"] is True
    assert "summary" not in raw


def test_run_is_create_only_and_seal_detects_tail_truncation(tmp_path: Path) -> None:
    executor = _Executor()
    approvals = 0

    def approval(
        plan: dict[str, Any], config: RunConfiguration,
        packets: tuple[dict[str, Any], ...], estimate_snapshot: dict[str, Any],
        execution_request_hash: str,
    ) -> dict[str, Any]:
        nonlocal approvals
        approvals += 1
        return _approval(
            plan, config, packets, estimate_snapshot, execution_request_hash,
        )

    runtime = _runtime(tmp_path, executor, approval_validator=approval)
    packets = [_packet("scope_and_cost_governor"), _packet("retrieval_governor")]
    config = _configuration(max_calls=2, max_workers=1)
    assert runtime.run(packets, config)["status"] == "completed"

    repeated = runtime.run(packets, config)
    assert repeated["reason"] == "preflight_rejected"
    assert approvals == 1
    assert executor.calls.count("retrieval_governor") == 1

    ledger = tmp_path / "data/governance/runs/run_fixture/events.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    ledger.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeStoreError, match="seal"):
        runtime.status("run_fixture")


def test_project_evidence_builder_enforces_core_locator_scope_snapshot_and_single_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "docs/evidence.md"
    source.parent.mkdir(parents=True)
    source.write_text("line one\nline two\nline three\n", encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    events = tmp_path / "data/events"
    daily = events / "daily/2026-08-04"
    daily.mkdir(parents=True)
    (events / "sources.jsonl").write_text(json.dumps({
        "source_id": "source_exact",
        "source_path": "docs/evidence.md",
        "source_sha256": source_hash,
    }) + "\n", encoding="utf-8")
    records = []
    for record_id, start, end in (("record_one", 1, 2), ("record_two", 2, 3)):
        records.append({
            "schema_version": "core/1.0",
            "record_id": record_id,
            "record_kind": "observation",
            "occurred_at": "2026-08-04T00:00:00+00:00",
            "recorded_at": "2026-08-04T00:00:00+00:00",
            "status": "completed",
            "created_by": {"actor_id": "actor_fixture", "actor_type": "human"},
            "payload": {"summary": record_id},
            "source_refs": [{
                "artifact_revision_id": f"artifact_evidence@sha256:{source_hash}",
                "locator": {
                    "kind": "line_range",
                    "path": "docs/evidence.md",
                    "start": start,
                    "end": end,
                },
                "verification_status": "integrity_verified",
            }],
        })
    (daily / "events.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
    )
    boundary_material = {
        "record_ids": ["record_one", "record_two"],
        "result_ids": [],
        "dataset_hashes": [],
        "model_hashes": [],
        "artifact_revisions": [],
        "commit_ids": [],
    }
    expected_passages = (
        EvidencePassage("record_one", "source_exact", "docs/evidence.md", source_hash, 1, 2, "line one\nline two"),
        EvidencePassage("record_two", "source_exact", "docs/evidence.md", source_hash, 2, 3, "line two\nline three"),
    )
    boundary = {**boundary_material, "snapshot_hash": "sha256:" + "0" * 64}
    packet = {
        "agent_id": "retrieval_governor",
        "scope": {"allowed_paths": ["docs/**"], "allowed_sources": ["canonical"]},
        "evidence_boundary": boundary,
    }
    builder = ProjectEvidenceBundleBuilder()
    preview = builder.preview(packet, tmp_path)
    packet["evidence_boundary"]["snapshot_hash"] = preview.snapshot_hash
    original = builder._read_project_file
    reads = 0

    def counted(root: Path, relative: str, *, max_bytes: int, label: str):
        nonlocal reads
        if relative == "docs/evidence.md":
            reads += 1
        return original(root, relative, max_bytes=max_bytes, label=label)

    monkeypatch.setattr(builder, "_read_project_file", counted)
    bundle = builder.build(packet, tmp_path)

    assert reads == 1
    assert bundle.passages == expected_passages
    assert bundle.approval_summary()["passages"][0]["source_id"] == "source_exact"
    assert "content" not in bundle.approval_summary()["passages"][0]

    outside = {**packet, "scope": {"allowed_paths": ["private/**"], "allowed_sources": ["canonical"]}}
    with pytest.raises(ValueError, match="allowed_paths"):
        ProjectEvidenceBundleBuilder().build(outside, tmp_path)

    records[0]["source_refs"][0]["verification_status"] = "unverified"
    (daily / "events.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not integrity verified"):
        ProjectEvidenceBundleBuilder().build(packet, tmp_path)


def test_evidence_builder_resolves_completed_amendment_and_rejects_ambiguous_source_id(
    tmp_path: Path,
) -> None:
    source = tmp_path / "docs/evidence.md"
    source.parent.mkdir(parents=True)
    source.write_text("evidence\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    events = tmp_path / "data/events"
    daily = events / "daily/2026-08-04"
    daily.mkdir(parents=True)
    (events / "sources.jsonl").write_text(
        json.dumps({
            "source_id": "source_exact",
            "source_path": "docs/evidence.md",
            "source_sha256": digest,
        }) + "\n",
        encoding="utf-8",
    )
    original = {
        "schema_version": "core/1.0",
        "record_id": "record_original",
        "record_kind": "observation",
        "occurred_at": "2026-08-04T00:00:00+00:00",
        "recorded_at": "2026-08-04T00:00:00+00:00",
        "status": "completed",
        "created_by": {"actor_id": "actor_fixture", "actor_type": "human"},
        "payload": {"summary": "recorded"},
        "source_refs": [{
            "artifact_revision_id": f"artifact_evidence@sha256:{digest}",
            "locator": {"path": "docs/evidence.md", "start": 1, "end": 1},
            "verification_status": "integrity_verified",
        }],
    }
    amendment = {
        "schema_version": "core/1.0",
        "record_id": "amendment_current",
        "record_kind": "amendment",
        "occurred_at": "2026-08-04T00:00:00+00:00",
        "recorded_at": "2026-08-04T00:00:00+00:00",
        "status": "completed",
        "created_by": {"actor_id": "actor_fixture", "actor_type": "human"},
        "relations": [{"type": "corrects", "target_id": "record_original"}],
        "payload": {
            "path": "/payload/summary",
            "recorded_value": "recorded",
            "corrected_value": "corrected",
            "reason": "fixture correction",
        },
    }
    (daily / "events.jsonl").write_text(
        json.dumps(original) + "\n" + json.dumps(amendment) + "\n",
        encoding="utf-8",
    )
    material = {
        "record_ids": ["record_original"], "result_ids": [],
        "dataset_hashes": [], "model_hashes": [], "artifact_revisions": [],
        "commit_ids": [],
    }
    packet = {
        "agent_id": "retrieval_governor",
        "scope": {"allowed_paths": ["docs/**"], "allowed_sources": ["canonical"]},
        "evidence_boundary": {
            **material,
            "snapshot_hash": artifact_hash({"placeholder": True}),
        },
    }
    resolved = ProjectEvidenceBundleBuilder().preview(packet, tmp_path)
    assert resolved.authority_records[0]["current_view"]["is_amended"] is True

    (daily / "events.jsonl").write_text(json.dumps(original) + "\n", encoding="utf-8")
    (events / "sources.jsonl").write_text(
        "\n".join((
            json.dumps({
                "source_id": "source_exact", "source_path": "docs/evidence.md",
                "source_sha256": digest,
            }),
            json.dumps({
                "source_id": "source_exact", "source_path": "docs/other.md",
                "source_sha256": "0" * 64,
            }),
        )) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="multiple path/hash revisions"):
        ProjectEvidenceBundleBuilder().build(packet, tmp_path)


def test_evidence_builder_rejects_noncryptographic_or_unsupported_boundary(tmp_path: Path) -> None:
    builder = ProjectEvidenceBundleBuilder()
    packet = {
        "agent_id": "scope_and_cost_governor",
        "scope": {"allowed_paths": ["docs/**"], "allowed_sources": ["canonical"]},
        "evidence_boundary": {
            "record_ids": [],
            "result_ids": [],
            "dataset_hashes": ["sha256:" + "a" * 64],
            "model_hashes": [],
            "artifact_revisions": [],
            "commit_ids": [],
            "snapshot_hash": "sha256:snapshot",
        },
    }
    with pytest.raises(ValueError, match="exact sha256"):
        builder.build(packet, tmp_path)

    packet["evidence_boundary"]["snapshot_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="unsupported"):
        builder.build(packet, tmp_path)
