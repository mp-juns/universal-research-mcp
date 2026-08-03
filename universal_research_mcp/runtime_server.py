"""Opt-in MCP surface for the independent research-agent runtime.

The default research-memory MCP remains read-only.  This separate server can
materialize and execute provider-backed agent sessions, but execution is
double-gated by a server-owner environment opt-in and a per-call approval.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Sequence

from mcp.server.fastmcp import FastMCP

from universal_research_mcp import __version__
from universal_research_mcp.agent_execution import (
    GenerationExecutorBundle,
    build_generation_executor,
)
from universal_research_mcp.agent_runtime import (
    AgentRuntime,
    RunConfiguration,
    RuntimeStoreError,
    SessionStore,
)
from universal_research_mcp.runtime.agent_approval import AgentApprovalStore


EXECUTION_ENABLE_ENV = "UNIVERSAL_RESEARCH_ENABLE_AGENT_EXECUTION"


def _default_root() -> Path:
    return Path(os.environ.get("UNIVERSAL_RESEARCH_ROOT", Path.cwd())).resolve()


ROOT = _default_root()

INSTRUCTIONS = """
This is the opt-in execution MCP for Universal Research agents. Preflight does
not call a model. A run is permitted only when the server owner set
UNIVERSAL_RESEARCH_ENABLE_AGENT_EXECUTION=1, the tool call sets
execution_approved=true, and every task packet contains the same approval_ref
inside its authority boundary. Provider credentials are resolved only from the
project's configured credential references. Never put API-key values in tool
arguments. Responses are summaries; prompt and raw-output artifacts stay in
the project-local runtime record.
""".strip()

mcp = FastMCP("Universal Research Agent Runtime", instructions=INSTRUCTIONS)


def configure_runtime(root: str | Path | None = None) -> None:
    """Select one project root before the stdio transport starts."""

    global ROOT
    ROOT = Path(root).expanduser().resolve() if root is not None else _default_root()


def _configuration(
    bundle: GenerationExecutorBundle,
    *,
    approval_ref: str,
    max_workers: int,
    max_calls: int,
    max_input_tokens: int,
    max_output_tokens: int,
    max_output_tokens_per_agent: int,
    max_cost_usd: float,
    timeout_seconds: float,
) -> RunConfiguration:
    return RunConfiguration(
        provider_id=bundle.provider_id,
        model=bundle.model,
        network_scope=bundle.network_scope,
        provider_configuration_hash=bundle.provider_configuration_hash,
        approval_ref=approval_ref,
        max_workers=max_workers,
        max_calls=max_calls,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_output_tokens_per_agent=max_output_tokens_per_agent,
        max_cost_usd=max_cost_usd,
        timeout_seconds=timeout_seconds,
    )


def _runtime_components(
    *,
    route: str,
    approval_ref: str,
    max_workers: int,
    max_calls: int,
    max_input_tokens: int,
    max_output_tokens: int,
    max_output_tokens_per_agent: int,
    max_cost_usd: float,
    input_cost_per_million_tokens_usd: float,
    output_cost_per_million_tokens_usd: float,
    timeout_seconds: float,
) -> tuple[AgentRuntime, RunConfiguration, GenerationExecutorBundle]:
    bundle = build_generation_executor(
        ROOT,
        route=route,
        max_calls=max_calls,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_output_tokens_per_agent=max_output_tokens_per_agent,
        max_cost_usd=max_cost_usd,
        input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
        output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
        request_timeout_seconds=timeout_seconds,
    )
    configuration = _configuration(
        bundle,
        approval_ref=approval_ref,
        max_workers=max_workers,
        max_calls=max_calls,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_output_tokens_per_agent=max_output_tokens_per_agent,
        max_cost_usd=max_cost_usd,
        timeout_seconds=timeout_seconds,
    )
    approval_store = AgentApprovalStore(ROOT)
    return AgentRuntime(
        ROOT,
        bundle.executor,
        approval_validator=approval_store.consume,
    ), configuration, bundle


def _blocked(reason: str, *, code: str, issues: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "agent-runtime-surface/1.0",
        "status": "blocked",
        "reason": reason,
        "issues": issues or [{"code": code, "message": reason}],
        "executed": False,
        "artifact_contents_included": False,
    }


def _safe_preflight(report: dict[str, Any], bundle: GenerationExecutorBundle) -> dict[str, Any]:
    return {
        "schema_version": report.get("schema_version", "agent-runtime-preflight/1.0"),
        "valid": report.get("valid") is True,
        "issues": report.get("issues") or [],
        "run_id": report.get("run_id"),
        "run_plan": _safe_run_plan(report.get("run_plan")),
        "run_plan_hash": report.get("run_plan_hash"),
        "estimate_snapshot_hash": report.get("estimate_snapshot_hash"),
        "execution_request_hash": report.get("execution_request_hash"),
        "estimates": report.get("estimates") or {},
        "provider": bundle.summary(),
        "executed": False,
        "artifact_contents_included": False,
    }


def _safe_run(report: dict[str, Any], bundle: GenerationExecutorBundle) -> dict[str, Any]:
    allowed = {
        key: report.get(key)
        for key in (
            "schema_version",
            "run_id",
            "run_plan_hash",
            "estimate_snapshot_hash",
            "execution_request_hash",
            "status",
            "reason",
            "issues",
            "claim_eligibility",
            "agent_result_count",
            "failure_count",
            "pending_failure_record_choices",
            "user_choice_required",
            "event_head_hash",
            "executed",
            "hidden_retries",
        )
        if key in report
    }
    allowed["provider"] = bundle.summary()
    allowed["provider_usage_estimate"] = bundle.executor.usage_snapshot()
    allowed["artifact_contents_included"] = False
    return allowed


def _safe_status(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "agent-runtime-status/1.0",
        "run_id": report.get("run_id"),
        "state": report.get("state"),
        "sessions": report.get("sessions") or {},
        "event_count": report.get("event_count", 0),
        "event_head_hash": report.get("event_head_hash"),
        "artifact_contents_included": False,
    }


def _safe_run_plan(report: object) -> dict[str, Any]:
    plan = report if isinstance(report, dict) else {}
    return {
        key: plan.get(key)
        for key in (
            "schema_version",
            "run_id",
            "workflow_id",
            "configuration",
            "configuration_hash",
            "tasks",
            "run_plan_hash",
        )
        if key in plan
    }


def _safe_session_inventory(report: object) -> list[dict[str, Any]]:
    sessions = report if isinstance(report, list) else []
    inventory: list[dict[str, Any]] = []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else None
        inventory.append({
            "session_id": item.get("session_id"),
            "agent_id": item.get("agent_id"),
            "artifact_names": list(item.get("artifact_names") or []),
            "decision": None if decision is None else {
                "status": decision.get("status"),
                "decision_hash": decision.get("decision_hash"),
                "finding_count": decision.get("finding_count", 0),
                "evidence_reference_count": decision.get(
                    "evidence_reference_count", 0,
                ),
            },
        })
    return inventory


def _safe_inspect(report: dict[str, Any], *, artifact_root: Path) -> dict[str, Any]:
    manifest = report.get("manifest") or {}
    return {
        "schema_version": "agent-runtime-inspection/1.0",
        "status": _safe_status(report.get("status") or {}),
        "manifest": {
            key: manifest.get(key)
            for key in (
                "schema_version",
                "run_id",
                "workflow_id",
                "created_at",
                "run_plan_hash",
                "configuration",
                "agent_ids",
            )
            if key in manifest
        },
        "run_plan": _safe_run_plan(report.get("run_plan")),
        "sessions": _safe_session_inventory(report.get("sessions")),
        "artifact_root": str(artifact_root),
        "artifact_contents_included": False,
    }


@mcp.tool()
def agent_runtime_preflight(
    packets: list[dict[str, Any]],
    route: str,
    approval_ref: str,
    max_workers: int,
    max_calls: int,
    max_input_tokens: int,
    max_output_tokens: int,
    max_output_tokens_per_agent: int,
    max_cost_usd: float,
    input_cost_per_million_tokens_usd: float,
    output_cost_per_million_tokens_usd: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Validate an exact provider-bound run plan without calling a model."""

    try:
        runtime, configuration, bundle = _runtime_components(
            route=route,
            approval_ref=approval_ref,
            max_workers=max_workers,
            max_calls=max_calls,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            max_output_tokens_per_agent=max_output_tokens_per_agent,
            max_cost_usd=max_cost_usd,
            input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
            output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
            timeout_seconds=timeout_seconds,
        )
        return _safe_preflight(runtime.preflight(packets, configuration), bundle)
    except (OSError, RuntimeError, ValueError) as exc:
        return _blocked(str(exc), code="RUNTIME-PREFLIGHT")


@mcp.tool()
def agent_runtime_run(
    packets: list[dict[str, Any]],
    route: str,
    approval_ref: str,
    execution_approved: bool,
    max_workers: int,
    max_calls: int,
    max_input_tokens: int,
    max_output_tokens: int,
    max_output_tokens_per_agent: int,
    max_cost_usd: float,
    input_cost_per_million_tokens_usd: float,
    output_cost_per_million_tokens_usd: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Execute one governed run only after both MCP execution gates pass."""

    if os.environ.get(EXECUTION_ENABLE_ENV) != "1":
        return _blocked(
            f"server execution is disabled; set {EXECUTION_ENABLE_ENV}=1 before starting this MCP",
            code="RUNTIME-SERVER-DISABLED",
        )
    if execution_approved is not True:
        return _blocked(
            "this run requires execution_approved=true",
            code="RUNTIME-RUN-NOT-APPROVED",
        )
    try:
        runtime, configuration, bundle = _runtime_components(
            route=route,
            approval_ref=approval_ref,
            max_workers=max_workers,
            max_calls=max_calls,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            max_output_tokens_per_agent=max_output_tokens_per_agent,
            max_cost_usd=max_cost_usd,
            input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
            output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
            timeout_seconds=timeout_seconds,
        )
        return _safe_run(runtime.run(packets, configuration), bundle)
    except (OSError, RuntimeError, ValueError) as exc:
        return _blocked(str(exc), code="RUNTIME-EXECUTION")


@mcp.tool()
def agent_runtime_status(run_id: str) -> dict[str, Any]:
    """Return hash-chain and session states without artifact contents."""

    try:
        return _safe_status(SessionStore(ROOT).status(run_id))
    except (OSError, RuntimeStoreError, ValueError) as exc:
        return _blocked(str(exc), code="RUNTIME-STATUS")


@mcp.tool()
def agent_runtime_inspect(run_id: str, agent_id: str | None = None) -> dict[str, Any]:
    """Return a concise run/session inventory; prompts and raw output stay private."""

    try:
        store = SessionStore(ROOT)
        return _safe_inspect(
            store.inspect(run_id, agent_id), artifact_root=store.run_dir(run_id),
        )
    except (OSError, RuntimeStoreError, ValueError) as exc:
        return _blocked(str(exc), code="RUNTIME-INSPECT")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the opt-in Universal Research agent MCP.")
    parser.add_argument(
        "--root",
        type=Path,
        help="Research project root (default: $UNIVERSAL_RESEARCH_ROOT or current directory).",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_runtime(args.root)
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
