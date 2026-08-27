"""Unified command line entry point for the research plugin and MCP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence

from universal_research_mcp.governance.registry import registry_report
from universal_research_mcp import __version__


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _root(value: Path | None, positional: Path | None = None) -> Path:
    selected = value or positional
    return (selected or Path(os.environ.get("UNIVERSAL_RESEARCH_ROOT", Path.cwd()))).resolve()



def _usage_command(root: Path, args: argparse.Namespace) -> int:
    """Report only token counts that a provider or host actually supplied."""

    from universal_research_mcp.core.usage import read_usage_observations, summarize_usage

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


def _secure_harness_command(root: Path, args: argparse.Namespace) -> int:
    from universal_research_mcp.secure_harness.approval import HarnessApprovalStore
    from universal_research_mcp.secure_harness.controller import (
        apply_changes,
        attest_run,
        build_plan_bundle,
        change_review,
        execute_codex,
        load_bundle,
        preflight,
        review_run,
    )
    from universal_research_mcp.secure_harness.docker_backend import doctor

    action = args.harness_action
    state_root = getattr(args, "state_root", None) or os.environ.get("UNIVERSAL_RESEARCH_HARNESS_STATE_ROOT")
    if action == "doctor":
        _emit(doctor())
        return 0
    if action == "plan":
        value = json.loads(args.specification.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("harness plan specification must be a JSON object")
        _emit(build_plan_bundle(root, value))
        return 0
    if action == "preflight":
        report = preflight(root, load_bundle(args.bundle))
        _emit(report)
        return 0 if report["valid"] else 2
    if action == "approve":
        bundle = load_bundle(args.bundle)
        report = preflight(root, bundle)
        if not report["valid"]:
            _emit(report)
            return 2
        _emit(HarnessApprovalStore(root, state_root=state_root).create(
            bundle["plan"],
            expected_plan_hash=args.expected_plan_hash,
            expires_at=args.expires_at,
        ))
        return 0
    if action == "run":
        if not args.execute_approved:
            _emit({
                "schema_version": "secure-harness-run/1.0",
                "status": "blocked",
                "reason": "explicit_execution_confirmation_missing",
                "executed": False,
            })
            return 2
        if args.prompt is None:
            raise ValueError("secure harness run requires --prompt")
        bundle = load_bundle(args.bundle)
        report = preflight(root, bundle)
        if not report["valid"]:
            _emit(report)
            return 2
        prompt = args.prompt.read_text(encoding="utf-8")
        _emit(execute_codex(root, bundle, prompt=prompt, state_root=state_root))
        return 0
    if action == "review":
        _emit(review_run(
            root, args.run_id, receipts_path=args.receipts, state_root=state_root,
        ))
        return 0
    if action == "attest":
        _emit(attest_run(
            root,
            args.run_id,
            expected_review_hash=args.confirm_review_hash,
            receipts_path=args.receipts,
            state_root=state_root,
        ))
        return 0
    if action == "changes":
        _emit(change_review(root, args.run_id, state_root=state_root))
        return 0
    if action == "apply":
        _emit(apply_changes(
            root,
            args.run_id,
            confirm_diff_hash=args.confirm_diff_hash,
            state_root=state_root,
        ))
        return 0
    raise ValueError("unsupported secure harness command")


def _refresh_after_canonical_append(root: Path, *, action: str, details: dict[str, Any]) -> int:
    """Refresh configured derived views, never concealing a successful append."""

    from universal_research_mcp.indexing import ensure_lexical_index, index_status
    from universal_research_mcp.semantic_runtime import (
        build_configured_semantic_index, configured_backend,
    )

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
    backend = configured_backend(root)
    semantic: dict[str, Any]
    if backend is None:
        semantic = {"status": "unconfigured", "executed": False}
    elif not backend.auto_refresh:
        semantic = {
            "status": "stale", "executed": False,
            "reason": "configured semantic refresh requires explicit build",
        }
    else:
        try:
            semantic = build_configured_semantic_index(root)
        except (OSError, RuntimeError, ValueError) as exc:
            semantic = {
                "status": "stale", "executed": False, "reason": str(exc),
                "recovery_command": f"universal-research semantic build --root {root}",
            }
    _emit({"status": "ok", "canonical_append_succeeded": True, "action": action,
           **details, "index": index, "semantic": semantic})
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


def _ingest_command(root: Path, args: argparse.Namespace) -> int:
    """Issue a receipt outside the MCP process after explicit user confirmation."""

    from universal_research_mcp.runtime.ingest_approval import IngestApprovalStore

    if args.ingest_action != "approve":
        raise ValueError("unsupported ingest command")
    if args.confirm_draft_sha256 != args.draft_sha256:
        raise ValueError("--confirm-draft-sha256 must exactly match --draft-sha256")
    _emit(IngestApprovalStore(root, state_root=args.state_root).issue(
        draft_id=args.draft_id,
        draft_sha256=args.draft_sha256,
        expires_at=args.expires_at,
    ))
    return 0


def _codex_agents_command(root: Path, args: argparse.Namespace) -> int:
    """Report the fail-closed Codex host-control availability boundary."""

    from universal_research_mcp.integrations.codex.agent_control import (
        codex_agent_status,
    )

    del root
    report = codex_agent_status()
    _emit(report)
    return 0 if report["status"] == "available" else 2


def _semantic_status(root: Path) -> dict[str, Any]:
    from universal_research_mcp.indexing import semantic_status

    return semantic_status(root)


def _public_semantic_status(root: Path) -> dict[str, Any]:
    """Report the configured offline backend without loading or downloading it."""

    from universal_research_mcp.indexing import semantic_status
    from universal_research_mcp.runtime.research_profile import (
        configuration_path as profile_configuration_path, load_profile,
    )
    from universal_research_mcp.runtime.semantic_config import configuration_path, load_semantic_config
    from universal_research_mcp.semantic_runtime import configured_backend

    config = load_semantic_config(root)
    configuration_source = "semantic_config"
    if config is None:
        profile = load_profile(root)
        if profile is not None:
            from universal_research_mcp.runtime.research_profile import semantic_config_from_profile

            config = semantic_config_from_profile(root)
            configuration_source = "research_profile" if config is not None else "none"
    if config is None:
        return {
            "status": "unconfigured",
            "configuration_path": str(configuration_path(root)),
            "research_profile_path": str(profile_configuration_path(root)),
            "semantic": semantic_status(root),
            "remote_used": False,
        }
    backend = configured_backend(root)
    assert backend is not None
    return {
        "status": "configured",
        "configuration_path": str(
            configuration_path(root) if configuration_source == "semantic_config" else profile_configuration_path(root)
        ),
        "configuration_source": configuration_source,
        "backend": config["backend"],
        "backend_class": backend.backend_class,
        "trained_embedding_model": backend.trained_embedding_model,
        "auto_refresh": backend.auto_refresh,
        "remote_used": False,
        "semantic": semantic_status(
            root,
            provider_id=backend.provider_id,
            model=backend.model,
            dimensions=backend.dimensions,
        ),
    }


def _public_semantic_build(root: Path, args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from universal_research_mcp.semantic_runtime import build_configured_semantic_index

    report = build_configured_semantic_index(root, batch_size=args.batch_size)
    return report, 0 if report.get("status") == "current" else 2


def _semantic_command(root: Path, args: argparse.Namespace) -> int:
    from universal_research_mcp.runtime.semantic_config import configure_demo, configure_local
    from universal_research_mcp.runtime.semantic_setup import catalogue, execute_setup, setup_plan

    if args.semantic_action == "status":
        _emit(_public_semantic_status(root))
        return 0
    if args.semantic_action == "models":
        _emit(catalogue())
        return 0
    if args.semantic_action == "setup":
        plan = setup_plan(
            root,
            model_id=args.model,
            manager=args.environment_manager,
            device=args.device,
            revision=args.revision,
            auto_refresh=args.auto_refresh,
            reuse_existing=args.reuse_existing,
        )
        if not args.execute:
            _emit(plan)
            return 0
        if not args.confirm_plan_sha256:
            raise ValueError("semantic setup execution requires --confirm-plan-sha256")
        _emit(execute_setup(plan, confirm_plan_sha256=args.confirm_plan_sha256))
        return 0
    if args.semantic_action == "configure":
        if args.backend == "demo":
            _emit(configure_demo(
                root, dimensions=args.dimensions or 256, auto_refresh=args.auto_refresh,
            ))
            return 0
        if args.model_path is None:
            raise ValueError("local semantic configuration requires --model-path")
        _emit(configure_local(
            root,
            model_path=args.model_path,
            device=args.device,
            trust_local_model_code=args.trust_local_model_code,
            dimensions=args.dimensions,
            auto_refresh=args.auto_refresh,
        ))
        return 0
    report, code = _public_semantic_build(root, args)
    _emit(report)
    return code


def _profile_command(root: Path, args: argparse.Namespace) -> int:
    """Manage only the declarative, project-local research profile."""

    from universal_research_mcp.runtime.research_profile import (
        profile_sha256, profile_status, profile_template, validate_profile, write_profile,
    )

    if args.profile_action == "template":
        _emit(profile_template())
        return 0
    if args.profile_action == "status":
        _emit(profile_status(root))
        return 0
    candidate = validate_profile(json.loads(args.input.read_text(encoding="utf-8")))
    digest = profile_sha256(candidate)
    if args.profile_action == "validate":
        _emit({
            "valid": True,
            "profile_sha256": digest,
            "execution": "not_executed",
            "provider_execution": "not_supported_by_public_mcp",
        })
        return 0
    if args.confirm_profile_sha256 != digest:
        raise ValueError("--confirm-profile-sha256 must exactly match the validated profile SHA-256")
    persisted = write_profile(root, candidate)
    _emit({
        "applied": True,
        "profile_sha256": profile_sha256(persisted),
        "execution": "not_executed",
        "provider_execution": "not_supported_by_public_mcp",
    })
    return 0


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
    serve.add_argument("--auto-index", action=argparse.BooleanOptionalAction, default=None)
    serve.add_argument("--startup-progress", action=argparse.BooleanOptionalAction, default=None)
    serve.add_argument("--legacy-tools", action="store_true")
    serve.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    serve.add_argument("--public-demo", action="store_true")
    serve.add_argument("--public-demo-manifest", type=Path, default=Path("config/public-demo.json"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--http-path", default="/mcp")
    serve.add_argument("--allowed-host", action="append", default=[])
    serve.add_argument("--allowed-origin", action="append", default=[])

    public_demo = subparsers.add_parser(
        "public-demo", help="Prepare or verify a content-bound public demo corpus.",
    )
    public_demo_actions = public_demo.add_subparsers(dest="public_demo_action", required=True)
    public_prepare = public_demo_actions.add_parser(
        "prepare", help="Write a reviewed publication manifest without starting a server.",
    )
    public_prepare.add_argument("--root", type=Path, required=True)
    public_prepare.add_argument("--corpus-id", required=True)
    public_prepare.add_argument("--display-name", required=True)
    public_prepare.add_argument("--manifest", type=Path, default=Path("config/public-demo.json"))
    public_prepare.add_argument(
        "--confirm-public-data", required=True,
        help="Exact disclosure acknowledgement printed in the documentation.",
    )
    public_verify = public_demo_actions.add_parser(
        "verify", help="Re-hash the reviewed corpus without serving or changing it.",
    )
    public_verify.add_argument("--root", type=Path, required=True)
    public_verify.add_argument("--manifest", type=Path, default=Path("config/public-demo.json"))

    initialize = subparsers.add_parser("init", help="Initialize an independent research store.")
    initialize.add_argument("path", nargs="?", type=Path)
    initialize.add_argument("--root", type=Path)

    index = subparsers.add_parser("index", help="Inspect or refresh derived search indexes.")
    index.add_argument("action", choices=("status", "ensure"))
    index.add_argument("--kind", choices=("lexical", "semantic", "all"), default="all")
    index.add_argument("--root", type=Path)
    index.add_argument("--dimensions", type=int)
    index.add_argument("--batch-size", type=int, default=32)

    build_index = subparsers.add_parser("build-index", help="Compatibility alias for lexical index ensure.")
    build_index.add_argument("--root", type=Path)

    semantic = subparsers.add_parser(
        "semantic", help="Configure, build, or inspect offline semantic candidate retrieval.",
    )
    semantic_actions = semantic.add_subparsers(dest="semantic_action", required=True)
    semantic_configure = semantic_actions.add_parser(
        "configure", help="Select an explicit offline semantic backend without running it.",
    )
    semantic_configure.add_argument("--root", type=Path)
    semantic_configure.add_argument("--backend", choices=("demo", "local"), required=True)
    semantic_configure.add_argument("--dimensions", type=int)
    semantic_configure.add_argument("--model-path", type=Path)
    semantic_configure.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    semantic_configure.add_argument("--trust-local-model-code", action="store_true")
    semantic_configure.add_argument("--auto-refresh", action=argparse.BooleanOptionalAction, default=False)
    semantic_build = semantic_actions.add_parser(
        "build", help="Build the configured semantic SQLite view without fallback.",
    )
    semantic_build.add_argument("--root", type=Path)
    semantic_build.add_argument("--batch-size", type=int, default=32)
    semantic_status = semantic_actions.add_parser("status", help="Inspect semantic configuration and index health.")
    semantic_status.add_argument("--root", type=Path)
    semantic_actions.add_parser("models", help="List the reviewed local SentenceTransformer recommendations.")
    semantic_setup = semantic_actions.add_parser(
        "setup", help="Plan or explicitly create an isolated local semantic environment and model snapshot.",
    )
    semantic_setup.add_argument("--root", type=Path)
    semantic_setup.add_argument("--model", required=True, help="Exact model ID from `semantic models`.")
    semantic_setup.add_argument("--revision", required=True, help="Full immutable model commit SHA (40 hex characters); branches and tags are rejected.")
    semantic_setup.add_argument("--environment-manager", choices=("auto", "conda", "venv"), default="auto")
    semantic_setup.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    semantic_setup.add_argument("--auto-refresh", action=argparse.BooleanOptionalAction, default=False)
    semantic_setup.add_argument("--reuse-existing", action="store_true")
    semantic_setup.add_argument("--execute", action="store_true", help="Run only with an exact confirmation hash from the displayed plan.")
    semantic_setup.add_argument("--confirm-plan-sha256")

    profile = subparsers.add_parser(
        "profile", help="Validate and apply a declarative research routing profile.",
    )
    profile_actions = profile.add_subparsers(dest="profile_action", required=True)
    profile_actions.add_parser("template", help="Print the safe default research profile JSON.")
    profile_validate = profile_actions.add_parser("validate", help="Validate profile JSON without writing or executing it.")
    profile_validate.add_argument("input", type=Path)
    profile_validate.add_argument("--root", type=Path)
    profile_apply = profile_actions.add_parser("apply", help="Write one validated profile after hash confirmation.")
    profile_apply.add_argument("input", type=Path)
    profile_apply.add_argument("--root", type=Path)
    profile_apply.add_argument("--confirm-profile-sha256", required=True)
    profile_status = profile_actions.add_parser("status", help="Inspect the applied profile without executing routes.")
    profile_status.add_argument("--root", type=Path)

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

    ingest = subparsers.add_parser(
        "ingest", help="Issue a separate host receipt for one prepared MCP ingest draft.",
    )
    ingest_actions = ingest.add_subparsers(dest="ingest_action", required=True)
    ingest_approve = ingest_actions.add_parser(
        "approve", help="Create a one-time signed receipt for one exact pending draft.",
    )
    ingest_approve.add_argument("--root", type=Path)
    ingest_approve.add_argument("--draft-id", required=True)
    ingest_approve.add_argument("--draft-sha256", required=True)
    ingest_approve.add_argument(
        "--confirm-draft-sha256", required=True,
        help="Repeat the exact draft SHA-256 after reviewing the pending ingest.",
    )
    ingest_approve.add_argument("--expires-at", required=True)
    ingest_approve.add_argument(
        "--state-root", type=Path,
        help="Optional absolute host-state location outside the research project.",
    )

    codex_agents = subparsers.add_parser(
        "codex-agents",
        help="Inspect whether protected Codex subagent control is available.",
    )
    codex_agent_actions = codex_agents.add_subparsers(
        dest="codex_agents_action", required=True,
    )
    codex_agent_status = codex_agent_actions.add_parser(
        "status", help="Report the protected host-control availability boundary.",
    )
    codex_agent_status.add_argument("--root", type=Path)

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

    harness = subparsers.add_parser(
        "harness", help="Plan and run a hash-bound Codex session through isolated Docker workers.",
    )
    harness_actions = harness.add_subparsers(dest="harness_action", required=True)
    harness_doctor = harness_actions.add_parser("doctor", help="Check the local Docker execution boundary without running a worker.")
    harness_doctor.add_argument("--root", type=Path)
    harness_plan = harness_actions.add_parser("plan", help="Build a sealed plan bundle without calling a model.")
    harness_plan.add_argument("specification", type=Path)
    harness_plan.add_argument("--root", type=Path)
    for action in ("preflight", "approve", "run"):
        command = harness_actions.add_parser(action)
        command.add_argument("bundle", type=Path)
        command.add_argument("--root", type=Path)
        command.add_argument("--state-root", type=Path)
        if action == "approve":
            command.add_argument("--expected-plan-hash", required=True)
            command.add_argument("--expires-at", required=True)
        elif action == "run":
            command.add_argument("--prompt", type=Path)
            command.add_argument("--execute-approved", action="store_true")
    harness_review = harness_actions.add_parser("review")
    harness_review.add_argument("run_id")
    harness_review.add_argument("--root", type=Path)
    harness_review.add_argument("--state-root", type=Path)
    harness_review.add_argument("--receipts", type=Path)
    harness_attest = harness_actions.add_parser(
        "attest", help="Create a one-time promotion attestation for a passed benchmark or final review.",
    )
    harness_attest.add_argument("run_id")
    harness_attest.add_argument("--root", type=Path)
    harness_attest.add_argument("--state-root", type=Path)
    harness_attest.add_argument("--receipts", type=Path)
    harness_attest.add_argument("--confirm-review-hash", required=True)
    harness_changes = harness_actions.add_parser("changes")
    harness_changes.add_argument("run_id")
    harness_changes.add_argument("--root", type=Path)
    harness_changes.add_argument("--state-root", type=Path)
    harness_apply = harness_actions.add_parser("apply")
    harness_apply.add_argument("run_id")
    harness_apply.add_argument("--root", type=Path)
    harness_apply.add_argument("--state-root", type=Path)
    harness_apply.add_argument("--confirm-diff-hash", required=True)
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
        auto_index = args.auto_index if args.auto_index is not None else not args.public_demo
        if auto_index:
            forwarded.append("--auto-index")
        if args.startup_progress is True:
            forwarded.append("--startup-progress")
        elif args.startup_progress is False:
            forwarded.append("--no-startup-progress")
        if args.legacy_tools:
            forwarded.append("--legacy-tools")
        if args.transport != "stdio":
            forwarded.extend(("--transport", args.transport))
        if args.public_demo:
            forwarded.append("--public-demo")
            forwarded.extend(("--public-demo-manifest", str(args.public_demo_manifest)))
        for option, value in (
            ("--host", args.host), ("--port", args.port), ("--http-path", args.http_path),
        ):
            forwarded.extend((option, str(value)))
        for value in args.allowed_host:
            forwarded.extend(("--allowed-host", value))
        for value in args.allowed_origin:
            forwarded.extend(("--allowed-origin", value))
        return serve_main(forwarded)

    if args.command == "public-demo":
        from universal_research_mcp.public_demo import (
            build_manifest, validate_manifest, write_manifest,
        )

        root = _root(args.root)
        if args.public_demo_action == "verify":
            _emit(validate_manifest(root, relative_path=args.manifest))
            return 0
        document = build_manifest(
            root,
            corpus_id=args.corpus_id,
            display_name=args.display_name,
            confirmation=args.confirm_public_data,
        )
        _emit(write_manifest(root, document, relative_path=args.manifest))
        return 0

    if args.command == "harness":
        return _secure_harness_command(_root(args.root), args)

    if args.command == "usage":
        return _usage_command(_root(args.root), args)

    if args.command == "source":
        return _source_command(_root(args.root), args)

    if args.command == "record":
        return _record_command(_root(getattr(args, "root", None)), args)

    if args.command == "ingest":
        return _ingest_command(_root(getattr(args, "root", None)), args)

    if args.command == "codex-agents":
        return _codex_agents_command(_root(getattr(args, "root", None)), args)

    if args.command == "semantic":
        return _semantic_command(_root(getattr(args, "root", None)), args)

    if args.command == "profile":
        return _profile_command(_root(getattr(args, "root", None)), args)

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
        report, code = _public_semantic_build(root, args)
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
    commands = {
        "serve", "init", "index", "build-index", "semantic", "profile",
        "public-demo", "doctor", "validate", "usage", "source", "record",
        "ingest", "codex-agents", "harness",
    }
    if materialized and materialized[0] in commands:
        return main(materialized)
    from universal_research_mcp.server import main as serve_main

    return serve_main(materialized)


if __name__ == "__main__":
    raise SystemExit(main())
