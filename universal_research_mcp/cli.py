"""Unified command line entry point for the research plugin and MCP."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence

from universal_research_mcp.governance.registry import registry_report
from universal_research_mcp import __version__


_INTERNAL_PROVIDER_PREVIEW_ENV = "UNIVERSAL_RESEARCH_INTERNAL_PROVIDER_PREVIEW"


def _internal_provider_preview_enabled() -> bool:
    """Return whether unsupported provider-development commands may be registered."""

    return os.environ.get(_INTERNAL_PROVIDER_PREVIEW_ENV) == "1"


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _root(value: Path | None, positional: Path | None = None) -> Path:
    selected = value or positional
    return (selected or Path(os.environ.get("UNIVERSAL_RESEARCH_ROOT", Path.cwd()))).resolve()


class _NoCallEmbedder:
    def embed(self, texts: tuple[str, ...], *, model: str, dimensions: int | None):
        raise RuntimeError("no embedding backend is configured")


def _cost_micros(value: str | None, *, required: bool) -> int:
    if value is None:
        if required:
            raise ValueError("remote semantic refresh requires --max-cost-usd")
        return 0
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("--max-cost-usd must be numeric") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("--max-cost-usd must be non-negative and finite")
    return int((amount * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))


def _provider_status(root: Path) -> dict[str, Any]:
    from universal_research_mcp.runtime.provider_config import provider_configuration_status

    return {
        **provider_configuration_status(root),
        "capabilities": {
            "openai-compatible-loopback": ["generation"],
            "openai": ["embedding", "generation"],
            "anthropic": ["generation"],
        },
        "remote_requires": ["explicit_per_run_approval", "provider_allowlist", "call_token_cost_budget"],
        "host_visualization_default": "off",
    }


def _read_packets(path: Path) -> list[dict[str, Any]]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        loaded = loaded.get("packets")
    if not isinstance(loaded, list) or not all(isinstance(packet, dict) for packet in loaded):
        raise ValueError("harness packet file must be a JSON array or an object with packets[]")
    return loaded


def _agent_inspection_summary(report: dict[str, Any], *, artifact_root: Path) -> dict[str, Any]:
    manifest = report.get("manifest") if isinstance(report.get("manifest"), dict) else {}
    plan = report.get("run_plan") if isinstance(report.get("run_plan"), dict) else {}
    sessions: list[dict[str, Any]] = []
    for item in report.get("sessions") or []:
        if not isinstance(item, dict):
            continue
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else None
        sessions.append({
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
    return {
        "schema_version": "agent-runtime-inspection/1.0",
        "status": report.get("status") or {},
        "manifest": {
            key: manifest.get(key)
            for key in (
                "schema_version", "run_id", "workflow_id", "created_at",
                "run_plan_hash", "configuration", "agent_ids",
            )
            if key in manifest
        },
        "run_plan": {
            key: plan.get(key)
            for key in (
                "schema_version", "run_id", "workflow_id", "configuration",
                "configuration_hash", "tasks", "run_plan_hash",
            )
            if key in plan
        },
        "sessions": sessions,
        "artifact_root": str(artifact_root),
        "artifact_contents_included": False,
    }


def _agent_command(root: Path, args: argparse.Namespace) -> int:
    """Preflight, execute, or inspect independent provider-backed sessions."""

    from universal_research_mcp.agent_execution import build_generation_executor
    from universal_research_mcp.agent_runtime import AgentRuntime, RunConfiguration, SessionStore
    from universal_research_mcp.runtime.agent_approval import (
        AgentApprovalError,
        AgentApprovalStore,
    )

    if args.agent_action in {"status", "inspect"}:
        store = SessionStore(root)
        if args.agent_action == "status":
            status = store.status(args.run_id)
            _emit({
                "schema_version": "agent-runtime-status/1.0",
                **status,
                "artifact_contents_included": False,
            })
        else:
            report = store.inspect(args.run_id, args.agent_id)
            _emit(_agent_inspection_summary(
                report, artifact_root=store.run_dir(args.run_id),
            ))
        return 0

    if args.agent_action == "run" and not args.execute_approved:
        _emit({
            "schema_version": "agent-runtime-run/1.0",
            "status": "blocked",
            "reason": "agent execution requires --execute-approved",
            "executed": False,
            "artifact_contents_included": False,
        })
        return 2

    packets = _read_packets(args.packets)
    maximum_cost_micros = _cost_micros(args.max_cost_usd, required=True)
    maximum_cost_usd = maximum_cost_micros / 1_000_000
    bundle = build_generation_executor(
        root,
        route=args.route,
        max_calls=args.max_calls,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        max_output_tokens_per_agent=args.max_output_tokens_per_agent,
        max_cost_usd=maximum_cost_usd,
        input_cost_per_million_tokens_usd=args.input_cost_per_million_tokens_usd,
        output_cost_per_million_tokens_usd=args.output_cost_per_million_tokens_usd,
        request_timeout_seconds=args.timeout_seconds,
    )
    configuration = RunConfiguration(
        provider_id=bundle.provider_id,
        model=bundle.model,
        network_scope=bundle.network_scope,
        provider_configuration_hash=bundle.provider_configuration_hash,
        approval_ref=args.approval_ref,
        max_workers=args.max_workers,
        max_calls=args.max_calls,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        max_output_tokens_per_agent=args.max_output_tokens_per_agent,
        max_cost_usd=maximum_cost_usd,
        timeout_seconds=args.timeout_seconds,
    )
    approval_store = AgentApprovalStore(root)
    runtime = AgentRuntime(
        root,
        bundle.executor,
        approval_validator=approval_store.consume,
    )
    if args.agent_action in {"preflight", "approve"}:
        report = runtime.preflight(packets, configuration)
        if args.agent_action == "approve":
            if report.get("valid") is not True:
                _emit({
                    "schema_version": "agent-execution-approval/2.0",
                    "status": "blocked",
                    "reason": "only a valid exact preflight plan can be approved",
                    "issues": report.get("issues") or [],
                    "executed": False,
                    "artifact_contents_included": False,
                })
                return 2
            try:
                grant = approval_store.create(
                    report["run_plan"],
                    configuration,
                    expected_run_plan_hash=args.expected_run_plan_hash,
                    expected_execution_request_hash=(
                        args.expected_execution_request_hash
                    ),
                    expires_at=args.expires_at,
                    estimate_snapshot_hash=report["estimate_snapshot_hash"],
                    execution_request_hash=report["execution_request_hash"],
                )
            except (AgentApprovalError, OSError) as exc:
                _emit({
                    "schema_version": "agent-execution-approval/2.0",
                    "status": "blocked",
                    "reason": str(exc),
                    "executed": False,
                    "artifact_contents_included": False,
                })
                return 2
            _emit({
                **grant,
                "status": "approved",
                "executed": False,
                "artifact_contents_included": False,
            })
            return 0
        _emit({
            "schema_version": report.get("schema_version", "agent-runtime-preflight/1.0"),
            "valid": report.get("valid") is True,
            "issues": report.get("issues") or [],
            "run_id": report.get("run_id"),
            "run_plan": {
                key: (report.get("run_plan") or {}).get(key)
                for key in (
                    "schema_version", "run_id", "workflow_id", "configuration",
                    "configuration_hash", "tasks", "run_plan_hash",
                )
                if key in (report.get("run_plan") or {})
            },
            "run_plan_hash": report.get("run_plan_hash"),
            "estimate_snapshot_hash": report.get("estimate_snapshot_hash"),
            "execution_request_hash": report.get("execution_request_hash"),
            "estimates": report.get("estimates") or {},
            "provider": bundle.summary(),
            "executed": False,
            "artifact_contents_included": False,
        })
        return 0 if report.get("valid") is True else 2

    report = runtime.run(packets, configuration)
    safe = {
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
    safe["provider"] = bundle.summary()
    safe["provider_usage_reservation"] = bundle.executor.usage_snapshot()
    from universal_research_mcp.harness import read_usage_observations, summarize_usage
    safe["usage_summary"] = summarize_usage(
        read_usage_observations(root), run_id=report.get("run_id"),
    )
    safe["artifact_contents_included"] = False
    _emit(safe)
    return 0 if report.get("status") == "completed" else 2


def _harness_command(root: Path, args: argparse.Namespace) -> int:
    if args.harness_action != "preflight":
        _emit({
            "schema_version": "parallel-harness-cli/2.0",
            "status": "blocked",
            "reason": (
                "direct harness execution is disabled; use agent preflight, "
                "agent approve, then agent run with one-time exact approval"
            ),
            "executed": False,
        })
        return 2

    from universal_research_mcp.harness import ParallelResearchHarness

    packets = _read_packets(args.packets)
    ceiling_micros = _cost_micros(args.max_cost_usd, required=False)
    ceiling_usd = ceiling_micros / 1_000_000
    report = ParallelResearchHarness(lambda _dispatch: {}).preflight(
        packets,
        max_workers=args.max_workers,
        aggregate_cost_ceiling_usd=ceiling_usd,
    )
    _emit(report)
    return 0 if report["valid"] else 2


def _usage_command(root: Path, args: argparse.Namespace) -> int:
    """Report only token counts that a provider or host actually supplied."""

    from universal_research_mcp.harness import read_usage_observations, summarize_usage

    summary = summarize_usage(read_usage_observations(root), run_id=args.run_id)
    _emit({
        **summary,
        "root": str(root),
        "host_telemetry_note": (
            "Codex host command, skill, and visualization tokens are excluded "
            "unless the host supplied an exact host_reported observation."
        ),
    })
    return 0


def _refresh_after_canonical_append(root: Path, *, action: str, details: dict[str, Any]) -> int:
    """Refresh derived lexical state, never concealing a successful append."""

    from universal_research_mcp.indexing import ensure_lexical_index, index_status

    try:
        index = ensure_lexical_index(root)
    except (OSError, RuntimeError, ValueError) as exc:
        _emit({
            "status": "stale", "canonical_append_succeeded": True,
            "action": action, **details, "index": index_status(root),
            "recovery_command": f"universal-research index ensure --kind lexical --root {root}",
            "reason": str(exc),
        })
        return 2
    _emit({"status": "ok", "canonical_append_succeeded": True, "action": action,
           **details, "index": index})
    return 0


def _source_command(root: Path, args: argparse.Namespace) -> int:
    from universal_research_mcp.core.input import register_source

    source = register_source(
        root, args.path, source_id=args.source_id, source_type=args.source_type,
    )
    return _refresh_after_canonical_append(root, action="source_register", details={"source": source})


def _record_command(root: Path, args: argparse.Namespace) -> int:
    from universal_research_mcp.core.input import (
        append_record, issues_json, read_record_input, sample_record,
        validate_candidate_records,
    )

    if args.record_action == "template":
        _emit(sample_record())
        return 0
    records = read_record_input(args.input)
    if args.record_action == "validate":
        issues = validate_candidate_records(root, records)
        _emit({"valid": not issues, "record_count": len(records), "issues": issues_json(issues)})
        return 0 if not issues else 2
    if len(records) != 1:
        raise ValueError("record append and record approve accept exactly one record")
    record = records[0]
    if args.record_action == "approve":
        if record.get("record_id") != args.confirm:
            raise ValueError("--confirm must exactly match the approval record_id")
        ledger = append_record(root, record, approval_bootstrap=True)
        return _refresh_after_canonical_append(root, action="record_approve", details={
            "record_id": record["record_id"], "ledger": str(ledger.relative_to(root)),
        })
    ledger = append_record(root, record, approval_ref=args.approval_ref)
    return _refresh_after_canonical_append(root, action="record_append", details={
        "record_id": record["record_id"], "approval_ref": args.approval_ref,
        "ledger": str(ledger.relative_to(root)),
    })


def _semantic_status(root: Path) -> dict[str, Any]:
    from universal_research_mcp.indexing import semantic_status

    return semantic_status(root)


def _ensure_semantic(root: Path, args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from universal_research_mcp.indexing import ensure_lexical_index, ensure_semantic_index
    from universal_research_mcp.providers import (
        CredentialResolver,
        LocalSentenceTransformerEmbedder,
        OpenAIProvider,
        ProviderRouter,
        RemoteBudget,
        RemotePolicy,
        RoutedSemanticEmbedder,
        UrllibTransport,
    )
    from universal_research_mcp.runtime.provider_config import load_provider_config

    lexical = ensure_lexical_index(root)
    config = load_provider_config(root)
    local_config = config["embedding"].get("local")
    local_preflight = {"available": False, "reason": "local embedding is not configured"}
    if local_config:
        local = LocalSentenceTransformerEmbedder(
            local_config["model_path"],
            device=local_config["device"],
            trust_local_model_code=local_config["trust_local_model_code"],
        )
        readiness = local.preflight()
        local_preflight = {"available": readiness.available, "reason": readiness.reason}
        if readiness.available:
            # A failure after local execution starts is terminal. It is never
            # converted into an automatic billable remote fallback.
            report = ensure_semantic_index(
                root,
                local,
                provider_id="local",
                model=str(Path(local_config["model_path"]).expanduser().resolve()),
                dimensions=args.dimensions,
                batch_size=args.batch_size,
            )
            return {"lexical": lexical, "local_preflight": local_preflight, "semantic": report}, 0

    remote_config = config["embedding"].get("remote")
    if not args.remote_approved:
        current = _semantic_status(root)
        # Fresh projects still receive a valid empty semantic DB without model
        # or API use. A populated project remains explicitly lexical-only.
        if int((lexical.get("verification") or {}).get("event_count", 0)) == 0:
            empty = ensure_semantic_index(
                root, _NoCallEmbedder(), provider_id="none", model="unconfigured",
                dimensions=None, batch_size=1,
            )
            return {"lexical": lexical, "local_preflight": local_preflight, "semantic": empty, "remote_used": False}, 0
        return {
            "lexical": lexical,
            "local_preflight": local_preflight,
            "semantic": current,
            "status": "setup_required",
            "reason": "local embedding unavailable; remote use needs explicit approval and budget",
            "remote_used": False,
        }, 2
    if not remote_config:
        raise ValueError("remote embedding is approved for this run but no remote embedding provider is configured")
    if remote_config["provider_id"] != "openai":
        raise ValueError("Anthropic is generation-only; configure OpenAI for embeddings")
    if args.max_calls is None or args.max_calls < 1:
        raise ValueError("remote semantic refresh requires positive --max-calls")
    if args.max_input_tokens is None or args.max_input_tokens < 1:
        raise ValueError("remote semantic refresh requires positive --max-input-tokens")
    if args.cost_per_million_tokens_usd is None:
        raise ValueError("remote semantic refresh requires --cost-per-million-tokens-usd")
    maximum_cost = _cost_micros(args.max_cost_usd, required=True)
    budget = RemoteBudget(
        max_calls=args.max_calls,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=0,
        max_estimated_cost_micros=maximum_cost,
    )
    policy = RemotePolicy(
        approved=True,
        allowed_provider_ids=frozenset({"openai"}),
        budget=budget,
    )
    provider = OpenAIProvider(
        transport=UrllibTransport(),
        credential_ref=remote_config["credential_ref"],
    )
    router = ProviderRouter(
        local=None,
        remotes=(provider,),
        credentials=CredentialResolver(),
    )
    embedder = RoutedSemanticEmbedder(
        router=router,
        remote_policy=policy,
        provider_id="openai",
        cost_per_million_tokens_usd=args.cost_per_million_tokens_usd,
    )
    report = ensure_semantic_index(
        root,
        embedder,
        provider_id="openai",
        model=remote_config["model"],
        dimensions=args.dimensions,
        batch_size=args.batch_size,
    )
    return {
        "lexical": lexical,
        "local_preflight": local_preflight,
        "semantic": report,
        "remote_used": True,
        "remote_usage_estimate": embedder.usage_snapshot(),
    }, 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="universal-research",
        description="Universal Research Memory, governance, and derived-index manager.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the unified MCP server.")
    serve.add_argument("--root", type=Path)
    serve.add_argument("--lexical-db", type=Path)
    serve.add_argument("--events-root", type=Path)
    serve.add_argument("--auto-index", action=argparse.BooleanOptionalAction, default=True)
    serve.add_argument("--legacy-tools", action="store_true")

    initialize = subparsers.add_parser("init", help="Initialize an independent research store.")
    initialize.add_argument("path", nargs="?", type=Path)
    initialize.add_argument("--root", type=Path)

    index = subparsers.add_parser("index", help="Inspect or refresh derived search indexes.")
    index.add_argument("action", choices=("status", "ensure"))
    index.add_argument("--kind", choices=("lexical", "semantic", "all"), default="all")
    index.add_argument("--root", type=Path)
    index.add_argument("--dimensions", type=int)
    index.add_argument("--batch-size", type=int, default=32)
    index.set_defaults(
        remote_approved=False,
        max_calls=None,
        max_input_tokens=None,
        max_cost_usd=None,
        cost_per_million_tokens_usd=None,
    )
    if _internal_provider_preview_enabled():
        index.add_argument("--remote-approved", action="store_true")
        index.add_argument("--max-calls", type=int)
        index.add_argument("--max-input-tokens", type=int)
        index.add_argument("--max-cost-usd")
        index.add_argument("--cost-per-million-tokens-usd")

    build_index = subparsers.add_parser("build-index", help="Compatibility alias for lexical index ensure.")
    build_index.add_argument("--root", type=Path)

    source = subparsers.add_parser("source", help="Register immutable project-contained source revisions.")
    source_actions = source.add_subparsers(dest="source_action", required=True)
    source_register = source_actions.add_parser("register", help="Append one new SHA-256 source revision.")
    source_register.add_argument("path", help="Project-relative source file path.")
    source_register.add_argument("--root", type=Path)
    source_register.add_argument("--source-id", required=True, help="Stable source ID beginning with src_.")
    source_register.add_argument("--source-type", required=True, help="Source format, for example markdown or text.")

    record = subparsers.add_parser("record", help="Validate or append governed canonical core records.")
    record_actions = record.add_subparsers(dest="record_action", required=True)
    record_actions.add_parser("template", help="Print a standard core record JSON example.")
    record_validate = record_actions.add_parser("validate", help="Validate JSON/JSONL without writing.")
    record_validate.add_argument("input", type=Path)
    record_validate.add_argument("--root", type=Path)
    record_append = record_actions.add_parser("append", help="Append one non-approval record with a prior approval.")
    record_append.add_argument("input", type=Path)
    record_append.add_argument("--root", type=Path)
    record_append.add_argument("--approval-ref", required=True)
    record_approve = record_actions.add_parser("approve", help="Bootstrap one human approval record.")
    record_approve.add_argument("input", type=Path)
    record_approve.add_argument("--root", type=Path)
    record_approve.add_argument("--confirm", required=True, help="Exact approval record ID to append.")

    usage = subparsers.add_parser(
        "usage", help="Summarize observed token usage without estimates.",
    )
    usage_actions = usage.add_subparsers(dest="usage_action", required=True)
    usage_summary = usage_actions.add_parser("summary", help="Read append-only token-usage observations.")
    usage_summary.add_argument("--root", type=Path)
    usage_summary.add_argument("--run-id")

    for command in ("doctor", "validate"):
        diagnostic = subparsers.add_parser(command, help="Report readiness without changing state.")
        diagnostic.add_argument("--root", type=Path)

    if _internal_provider_preview_enabled():
        provider = subparsers.add_parser("provider", help=argparse.SUPPRESS)
        provider_actions = provider.add_subparsers(dest="provider_action", required=True)
        provider_status = provider_actions.add_parser("status")
        provider_status.add_argument("--root", type=Path)
        remote = provider_actions.add_parser("configure-remote")
        remote.add_argument("--root", type=Path)
        remote.add_argument("--capability", choices=("embedding", "generation"), required=True)
        remote.add_argument("--provider", choices=("openai", "anthropic"), required=True)
        remote.add_argument("--model", required=True)
        remote.add_argument("--credential-ref", required=True, help="env:NAME or keyring:SERVICE/ACCOUNT; never a key value")
        local = provider_actions.add_parser("configure-local-embedding")
        local.add_argument("--root", type=Path)
        local.add_argument("--model-path", type=Path, required=True)
        local.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
        local.add_argument("--trust-local-model-code", action="store_true")
        loopback = provider_actions.add_parser(
            "configure-loopback-generation",
            help="Configure a literal loopback OpenAI-compatible generation endpoint.",
        )
        loopback.add_argument("--root", type=Path)
        loopback.add_argument("--endpoint", required=True, help="Exactly http://127.0.0.1:PORT/v1 or http://[::1]:PORT/v1")
        loopback.add_argument("--model", required=True)
        loopback.add_argument(
            "--credential-ref",
            help="Optional env:NAME or keyring:SERVICE/ACCOUNT reference; never a key value.",
        )

        harness = subparsers.add_parser("harness", help=argparse.SUPPRESS)
        harness.add_argument("harness_action", choices=("preflight", "run"))
        harness.add_argument("packets", type=Path)
        harness.add_argument("--root", type=Path)
        harness.add_argument("--max-workers", type=int, default=1)
        harness.add_argument("--remote-approved", action="store_true")
        harness.add_argument("--max-calls", type=int)
        harness.add_argument("--max-input-tokens", type=int)
        harness.add_argument("--max-total-output-tokens", type=int)
        harness.add_argument("--max-output-tokens-per-agent", type=int)
        harness.add_argument("--max-cost-usd")
        harness.add_argument("--input-cost-per-million-tokens-usd")
        harness.add_argument("--output-cost-per-million-tokens-usd")

        agent = subparsers.add_parser("agent", help=argparse.SUPPRESS)
        agent_actions = agent.add_subparsers(dest="agent_action", required=True)
        for action in ("preflight", "approve", "run"):
            runtime = agent_actions.add_parser(action)
            runtime.add_argument("packets", type=Path)
            runtime.add_argument("--root", type=Path)
            runtime.add_argument("--route", choices=("loopback", "remote"), required=True)
            runtime.add_argument("--approval-ref", required=True)
            runtime.add_argument("--max-workers", type=int, default=1)
            runtime.add_argument("--max-calls", type=int, required=True)
            runtime.add_argument("--max-input-tokens", type=int, required=True)
            runtime.add_argument(
                "--max-total-output-tokens",
                dest="max_output_tokens",
                type=int,
                required=True,
            )
            runtime.add_argument("--max-output-tokens-per-agent", type=int, required=True)
            runtime.add_argument("--max-cost-usd", required=True)
            runtime.add_argument("--input-cost-per-million-tokens-usd", required=True)
            runtime.add_argument("--output-cost-per-million-tokens-usd", required=True)
            runtime.add_argument("--timeout-seconds", type=float, default=60.0)
            if action == "run":
                runtime.add_argument(
                    "--execute-approved",
                    action="store_true",
                    help="Confirm this exact CLI invocation may call the configured model route.",
                )
            elif action == "approve":
                runtime.add_argument(
                    "--expected-run-plan-hash",
                    required=True,
                    help="Exact run_plan_hash returned by the preceding preflight.",
                )
                runtime.add_argument(
                    "--expected-execution-request-hash",
                    required=True,
                    help=(
                        "Exact execution_request_hash returned by the preceding preflight; "
                        "it binds the plan, provider configuration, and cost reservations."
                    ),
                )
                runtime.add_argument(
                    "--expires-at",
                    required=True,
                    help="Timezone-qualified ISO-8601 expiry for this one-time approval.",
                )
        agent_status = agent_actions.add_parser("status")
        agent_status.add_argument("run_id")
        agent_status.add_argument("--root", type=Path)
        agent_inspect = agent_actions.add_parser("inspect")
        agent_inspect.add_argument("run_id")
        agent_inspect.add_argument("--root", type=Path)
        agent_inspect.add_argument("--agent-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        from universal_research_mcp.server import main as serve_main

        forwarded: list[str] = []
        for option, value in (("--root", args.root), ("--lexical-db", args.lexical_db), ("--events-root", args.events_root)):
            if value is not None:
                forwarded.extend((option, str(value)))
        if args.auto_index:
            forwarded.append("--auto-index")
        if args.legacy_tools:
            forwarded.append("--legacy-tools")
        return serve_main(forwarded)

    if args.command == "provider":
        from universal_research_mcp.runtime.provider_config import (
            configure_local_embedding,
            configure_loopback_generation,
            configure_remote_provider,
        )

        root = _root(args.root)
        if args.provider_action == "status":
            _emit(_provider_status(root))
        elif args.provider_action == "configure-local-embedding":
            _emit(configure_local_embedding(
                root,
                model_path=args.model_path,
                device=args.device,
                trust_local_model_code=args.trust_local_model_code,
            ))
        elif args.provider_action == "configure-loopback-generation":
            _emit(configure_loopback_generation(
                root,
                endpoint=args.endpoint,
                model=args.model,
                credential_ref=args.credential_ref,
            ))
        else:
            _emit(configure_remote_provider(
                root,
                capability=args.capability,
                provider_id=args.provider,
                model=args.model,
                credential_ref=args.credential_ref,
            ))
        return 0

    if args.command == "harness":
        return _harness_command(_root(args.root), args)

    if args.command == "agent":
        return _agent_command(_root(args.root), args)

    if args.command == "usage":
        return _usage_command(_root(args.root), args)

    if args.command == "source":
        return _source_command(_root(args.root), args)

    if args.command == "record":
        return _record_command(_root(getattr(args, "root", None)), args)

    from universal_research_mcp.indexing import (
        ensure_lexical_index,
        index_status,
        initialize_project,
        semantic_status,
    )

    root = _root(args.root, getattr(args, "path", None))
    if args.command == "init":
        lexical = initialize_project(root)
        # Initialization creates only canonical + lexical state.  A zero-vector
        # placeholder must not masquerade as an available semantic index.
        semantic = semantic_status(root)
        _emit({"root": str(root), "lexical": lexical, "semantic": semantic})
        return 0
    if args.command == "build-index":
        report = ensure_lexical_index(root)
        _emit(report)
        return 0
    if args.command == "index":
        if args.action == "status":
            report = {
                "root": str(root),
                "lexical": index_status(root),
                "semantic": semantic_status(root),
            }
            _emit(report if args.kind == "all" else {args.kind: report[args.kind]})
            requested = report.values() if args.kind == "all" else (report[args.kind],)
            return 0 if all(not isinstance(item, dict) or item.get("status") == "current" for item in requested) else 2
        if args.kind == "lexical":
            report = ensure_lexical_index(root)
            _emit(report)
            return 0
        report, code = _ensure_semantic(root, args)
        if args.kind == "semantic":
            report = {key: value for key, value in report.items() if key != "lexical"}
        _emit(report)
        return code

    report = {
        "root": str(root),
        "lexical": index_status(root),
        "semantic": semantic_status(root),
        "governance": registry_report(),
        "host_integration": {
            "supported": ["codex"],
            "model_execution": "codex_host_owned",
            "external_provider_execution": "unsupported",
            "host_visualization_default": "off",
        },
    }
    _emit(report)
    return 0 if report["lexical"].get("status") == "current" else 2


def legacy_main(argv: Sequence[str] | None = None) -> int:
    """Preserve ``universal-research-mcp --root ...`` while adding subcommands."""

    materialized = list(sys.argv[1:] if argv is None else argv)
    commands = {"serve", "init", "index", "build-index", "doctor", "validate", "usage", "source", "record"}
    if _internal_provider_preview_enabled():
        commands.update({"provider", "harness", "agent"})
    if materialized and materialized[0] in commands:
        return main(materialized)
    from universal_research_mcp.server import main as serve_main

    return serve_main(materialized)


if __name__ == "__main__":
    raise SystemExit(main())
