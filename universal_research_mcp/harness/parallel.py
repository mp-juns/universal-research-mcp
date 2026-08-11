"""Parallel host harness with deterministic governance and no hidden retries."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Protocol

from governance.failure_policy import build_failure_record, resolve_failure_policy
from governance.hashing import artifact_hash
from governance.registry import CRITICAL, SCOPE_AND_COST_GOVERNOR
from governance.validation import validate_scope_governor_decision, validate_task_packet
from integrations.codex.adapter import (
    build_dispatch_request,
    build_scope_governor_receipt,
    capture_decision,
    validate_dispatch_manifest,
    validate_critical_review_batch,
)


class AgentExecutor(Protocol):
    def __call__(self, dispatch: dict[str, Any]) -> dict[str, Any]: ...


class RecordSink(Protocol):
    def __call__(self, record: dict[str, Any]) -> bool | None: ...


@dataclass(frozen=True)
class _Outcome:
    index: int
    packet: dict[str, Any]
    captured: dict[str, Any] | None
    failure: dict[str, Any] | None


class ParallelResearchHarness:
    """Execute already-approved packets through an injected host executor.

    The harness has no model, network, filesystem, or process-kill capability
    of its own. It submits at most ``max_workers`` calls, never retries, and
    cannot forcibly stop a call that an external provider already accepted.
    """

    def __init__(self, executor: AgentExecutor, record_sink: RecordSink | None = None) -> None:
        self._executor = executor
        self._record_sink = record_sink

    def run(
        self,
        packets: list[dict[str, Any]],
        *,
        max_workers: int,
        aggregate_cost_ceiling_usd: float,
        declared_costs_usd: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        issues = self._validate_batch(
            packets,
            max_workers=max_workers,
            aggregate_cost_ceiling_usd=aggregate_cost_ceiling_usd,
            declared_costs_usd=declared_costs_usd,
        )
        if issues:
            return self._blocked("preflight_rejected", issues=issues)

        governor_packet = next(
            packet for packet in packets if packet["agent_id"] == SCOPE_AND_COST_GOVERNOR
        )
        governor = self._execute_one(-1, governor_packet)
        if governor.failure is not None:
            failure_record, recorded = self._record_failure(governor_packet, governor.failure)
            return self._blocked(
                "scope_governor_failed",
                governor=governor.captured,
                failures=[failure_record],
                records_complete=recorded,
            )
        governor_decision = (governor.captured or {}).get("decision") or {}
        governor_recorded = self._record_decision(governor_packet, governor.captured or {})
        governor_contract_issues = validate_scope_governor_decision(governor_decision)
        if governor_decision.get("status") != "pass" or governor_contract_issues:
            return self._blocked(
                "scope_governor_reapproval_required",
                governor=governor.captured,
                issues=[*governor_contract_issues, {
                    "code": "GOV-PLAN-001",
                    "message": "scope_and_cost_governor did not return an actionable passing preflight",
                }],
                records_complete=governor_recorded,
            )

        workers = [packet for packet in packets if packet is not governor_packet]
        governor_receipt: dict[str, Any] | None = None
        receipt_recorded = True
        if workers:
            receipt_result = build_scope_governor_receipt(
                governor_packet, governor.captured or {}, workers,
            )
            if not receipt_result.get("valid"):
                return self._blocked(
                    "scope_governor_receipt_invalid",
                    governor=governor.captured,
                    issues=receipt_result.get("issues") or [],
                    records_complete=governor_recorded,
                )
            governor_receipt = receipt_result["receipt"]
            receipt_recorded = self._record(governor_receipt)
        outcomes, failures, records_complete = self._run_workers(
            workers, max_workers, governor_receipt,
        )
        records_complete = records_complete and governor_recorded and receipt_recorded
        material_decisions = [
            (outcome.captured or {}).get("decision") or {}
            for outcome in outcomes
            if outcome.captured and outcome.captured.get("accepted")
        ]
        adverse = any(
            decision.get("status") in {"fail", "blocked", "inconclusive"}
            for decision in material_decisions
        )
        status = "blocked" if failures else "completed"
        return {
            "schema_version": "parallel-research-run/1.0",
            "status": status,
            "reason": "worker_failure" if failures else "completed_without_execution_failure",
            "governor": governor.captured,
            "scope_governor_receipt_hash": (
                governor_receipt.get("receipt_hash") if governor_receipt else None
            ),
            "results": [self._render_outcome(outcome) for outcome in outcomes],
            "failures": failures,
            "records_complete": records_complete,
            "claim_eligibility": (
                "eligible" if status == "completed" and records_complete and not adverse
                else "blocked"
            ),
            "hidden_retries": 0,
            "force_killed_calls": 0,
        }

    def preflight(
        self,
        packets: list[dict[str, Any]],
        *,
        max_workers: int,
        aggregate_cost_ceiling_usd: float,
        declared_costs_usd: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        """Validate the complete batch without invoking the executor."""

        issues = self._validate_batch(
            packets,
            max_workers=max_workers,
            aggregate_cost_ceiling_usd=aggregate_cost_ceiling_usd,
            declared_costs_usd=declared_costs_usd,
        )
        return {
            "schema_version": "parallel-research-preflight/1.0",
            "valid": not issues,
            "issues": issues,
            "packet_count": len(packets),
            "max_workers": max_workers,
            "aggregate_cost_ceiling_usd": aggregate_cost_ceiling_usd,
            "executed": False,
        }

    def _validate_batch(
        self,
        packets: list[dict[str, Any]],
        *,
        max_workers: int,
        aggregate_cost_ceiling_usd: float,
        declared_costs_usd: Mapping[str, float] | None,
    ) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
            issues.append({"code": "GOV-COST-001", "message": "max_workers must be positive"})
        if (
            not isinstance(aggregate_cost_ceiling_usd, (int, float))
            or isinstance(aggregate_cost_ceiling_usd, bool)
            or not isfinite(float(aggregate_cost_ceiling_usd))
            or aggregate_cost_ceiling_usd < 0
        ):
            issues.append({"code": "GOV-COST-001", "message": "aggregate cost ceiling is invalid"})
            return issues
        if not packets:
            return [{"code": "GOV-PLAN-001", "message": "at least one task packet is required"}]
        identities = [packet.get("agent_id") for packet in packets if isinstance(packet, dict)]
        if identities.count(SCOPE_AND_COST_GOVERNOR) != 1:
            issues.append({
                "code": "GOV-PLAN-001",
                "message": "exactly one scope_and_cost_governor packet is required",
            })
        if len(identities) != len(set(identities)):
            issues.append({"code": "GOV-REGISTRY-002", "message": "agent packets must be unique"})
        for packet in packets:
            issues.extend(validate_task_packet(packet))

        workers = [packet for packet in packets if packet.get("agent_id") != SCOPE_AND_COST_GOVERNOR]
        critical = [packet for packet in workers if packet.get("agent_id") in CRITICAL]
        if critical:
            if len(critical) != len(workers):
                issues.append({"code": "GOV-REGISTRY-002", "message": "critical and operational batches cannot be mixed"})
            else:
                issues.extend(validate_critical_review_batch(critical))

        if workers:
            parallel_ceiling = min(int(packet["scope"]["max_parallelism"]) for packet in workers)
            if max_workers > parallel_ceiling:
                issues.append({"code": "GOV-COST-001", "message": "max_workers exceeds task scope"})
        total_cost = 0.0
        for packet in packets:
            agent_id = str(packet.get("agent_id"))
            if declared_costs_usd is None:
                raw_cost = (packet.get("scope") or {}).get("estimated_cost_usd")
            elif agent_id not in declared_costs_usd:
                issues.append({
                    "code": "GOV-COST-001",
                    "message": f"explicit declared cost is missing for {agent_id}",
                })
                continue
            else:
                raw_cost = declared_costs_usd[agent_id]
            if (
                not isinstance(raw_cost, (int, float))
                or isinstance(raw_cost, bool)
                or not isfinite(float(raw_cost))
                or raw_cost < 0
            ):
                issues.append({"code": "GOV-COST-001", "message": f"missing or invalid declared cost for {agent_id}"})
                continue
            cost = float(raw_cost)
            if cost > float(packet["scope"]["max_cost_usd"]):
                issues.append({"code": "GOV-COST-001", "message": f"declared cost exceeds {agent_id} scope"})
            total_cost += cost
        if total_cost > float(aggregate_cost_ceiling_usd):
            issues.append({"code": "GOV-COST-001", "message": "declared aggregate cost exceeds ceiling"})
        return issues

    def _run_workers(
        self,
        packets: list[dict[str, Any]],
        max_workers: int,
        governor_receipt: dict[str, Any] | None,
    ) -> tuple[list[_Outcome], list[dict[str, Any]], bool]:
        if not packets:
            return [], [], True
        results: list[_Outcome | None] = [None] * len(packets)
        failures: list[dict[str, Any]] = []
        records_complete = True
        next_index = 0
        active: dict[Future[_Outcome], int] = {}
        blocked = False
        pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="urag-agent")
        try:
            while next_index < len(packets) and len(active) < max_workers:
                future = pool.submit(
                    self._execute_one, next_index, packets[next_index], governor_receipt,
                )
                active[future] = next_index
                next_index += 1
            while active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    index = active.pop(future)
                    outcome = future.result()
                    results[index] = outcome
                    if outcome.failure is not None:
                        failure_record, recorded = self._record_failure(outcome.packet, outcome.failure)
                        failures.append(failure_record)
                        records_complete = records_complete and recorded
                        blocked = True
                    elif outcome.captured is not None:
                        records_complete = records_complete and self._record_decision(
                            outcome.packet, outcome.captured,
                        )
                if blocked:
                    for future in active:
                        future.cancel()
                    # Active external calls are allowed to return safely. No
                    # force-kill and no further packets are submitted.
                    continue
                while next_index < len(packets) and len(active) < max_workers:
                    future = pool.submit(
                        self._execute_one, next_index, packets[next_index], governor_receipt,
                    )
                    active[future] = next_index
                    next_index += 1
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        materialized = [outcome for outcome in results if outcome is not None]
        return materialized, failures, records_complete

    def _execute_one(
        self,
        index: int,
        packet: dict[str, Any],
        governor_receipt: dict[str, Any] | None = None,
    ) -> _Outcome:
        dispatch = build_dispatch_request(packet, governor_receipt)
        if not dispatch.get("dispatchable"):
            return _Outcome(index, packet, None, {
                "classification": "policy_violation",
                "code": "GOV-DISPATCH-INVALID",
                "blocking": True,
                "detail": {"issues": dispatch.get("issues") or []},
            })
        pinned_dispatch_hash = str(dispatch.get("dispatch_hash") or "")
        dispatch_issues = validate_dispatch_manifest(
            dispatch,
            expected_manifest_hash=pinned_dispatch_hash,
        )
        if dispatch_issues:
            return _Outcome(index, packet, None, {
                "classification": "policy_violation",
                "code": "GOV-DISPATCH-INVALID",
                "blocking": True,
                "detail": {"issues": dispatch_issues},
            })
        try:
            decision = self._executor(dispatch)
        except Exception as exc:
            classification = (
                "validation_failure"
                if getattr(exc, "code", None) == "provider_output_invalid"
                else "execution_failure"
            )
            return _Outcome(index, packet, None, {
                "classification": classification,
                "code": "EXEC-AGENT",
                "blocking": True,
                "detail": {"type": type(exc).__name__, "message": str(exc)},
            })
        captured = capture_decision(packet, decision)
        if not captured.get("accepted"):
            return _Outcome(index, packet, captured, {
                "classification": "validation_failure",
                "code": "VALIDATION-AGENT-OUTPUT",
                "blocking": True,
                "detail": {"issues": captured.get("issues") or []},
            })
        return _Outcome(index, packet, captured, None)

    def _record_failure(
        self,
        packet: dict[str, Any],
        failure: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        material = {
            **failure,
            "run_id": packet.get("run_id"),
            "workflow_id": packet.get("workflow_id"),
            "agent_id": packet.get("agent_id"),
            "operation_id": f"dispatch:{packet.get('agent_id')}",
        }
        record = build_failure_record(
            material,
            resolve_failure_policy(task=packet, environ={}),
        )
        return record, self._record(record)

    def _record_decision(self, packet: dict[str, Any], captured: dict[str, Any]) -> bool:
        if not captured.get("accepted"):
            return False
        agent_id = packet.get("agent_id")
        decision_hash = captured.get("decision_hash")
        return self._record({
            "schema_version": "harness-agent-record/1.0",
            "record_type": "validated_agent_decision",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": packet.get("run_id"),
            "workflow_id": packet.get("workflow_id"),
            "agent_id": agent_id,
            "user_visible_summary": f"Validated decision recorded for {agent_id}.",
            "internal_artifact_refs": [
                value for value in (decision_hash, artifact_hash(packet)) if value
            ],
            "commands_or_operations": [f"agent_dispatch:{agent_id}"],
            "authority_basis": "approved task packet and validated role decision",
            "chat_disclosure": {
                "mode": "summary_only",
                "reason": "default central manager disclosure policy",
            },
            "task_packet_hash": artifact_hash(packet),
            "decision_hash": decision_hash,
            "decision": captured.get("decision"),
        })

    def _record(self, record: dict[str, Any]) -> bool:
        if self._record_sink is None:
            return False
        try:
            result = self._record_sink(record)
        except Exception:
            return False
        return result is not False

    @staticmethod
    def _render_outcome(outcome: _Outcome) -> dict[str, Any]:
        return {
            "agent_id": outcome.packet.get("agent_id"),
            "accepted": bool(outcome.captured and outcome.captured.get("accepted")),
            "decision": (outcome.captured or {}).get("decision"),
            "failed": outcome.failure is not None,
        }

    @staticmethod
    def _blocked(
        reason: str,
        *,
        issues: list[dict[str, Any]] | None = None,
        governor: dict[str, Any] | None = None,
        failures: list[dict[str, Any]] | None = None,
        records_complete: bool = False,
    ) -> dict[str, Any]:
        return {
            "schema_version": "parallel-research-run/1.0",
            "status": "blocked",
            "reason": reason,
            "issues": issues or [],
            "governor": governor,
            "results": [],
            "failures": failures or [],
            "records_complete": records_complete,
            "claim_eligibility": "blocked",
            "hidden_retries": 0,
            "force_killed_calls": 0,
        }
