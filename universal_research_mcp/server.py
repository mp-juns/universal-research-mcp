"""Unified MCP surface for research memory and deterministic governance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Literal, Sequence

from mcp.server.fastmcp import FastMCP

from universal_research_mcp.core.audit import audit_report
from universal_research_mcp.core.claim_gate import evaluate_claim_gate
from universal_research_mcp.core.ledger import read_jsonl
from universal_research_mcp.core.search import safe_fts_query
from universal_research_mcp.governance.escalation import evaluate_gate
from universal_research_mcp.governance.failure_policy import build_failure_record, resolve_failure_policy
from universal_research_mcp.governance.prompts import load_prompt_pack, prompt_registry_report
from universal_research_mcp.governance.registry import GOVERNANCE_VERSION, load_registry, manifest_hash, registry_report
from universal_research_mcp.governance.scope_policy import assess_plan_necessity, operation_gate
from universal_research_mcp.governance.validation import validate_decision, validate_task_packet
from universal_research_mcp.integrations.codex.adapter import (
    build_critical_review_batch,
    build_dispatch_request,
    build_scope_governor_receipt,
    capture_decision,
)
from universal_research_mcp import __version__


def _default_root() -> Path:
    return Path(os.environ.get("UNIVERSAL_RESEARCH_ROOT", Path.cwd())).resolve()


ROOT = _default_root()
RESEARCH_DB = Path(os.environ.get("UNIVERSAL_RESEARCH_LEXICAL_DB", ROOT / "data/index/research.sqlite")).resolve()
EVENTS_ROOT = Path(os.environ.get("UNIVERSAL_RESEARCH_EVENTS_ROOT", ROOT / "data/events")).resolve()
MAX_FETCH_LINES = int(os.environ.get("UNIVERSAL_RESEARCH_MAX_FETCH_LINES", "500"))
INDEX_STARTUP_STATUS: dict[str, Any] = {"status": "not_requested"}

DENIED_BASENAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "authorized_keys", "credentials.json"}
DENIED_FRAGMENTS = {"secret", "token", "credential", "private_key", "api_key", "apikey"}

INSTRUCTIONS = """
Use memory_search_candidates to find candidate research records. A search score
is not evidence. Before making an important claim, call memory_fetch_evidence
with the exact path and line range returned by search, then report that source
range and its hash. Before reporting a material result, comparison, causal,
release, or other load-bearing factual claim, call memory_gate_claim with the
exact fetched evidence references; do not state the claim when the gate blocks
it. Governance tools validate plans, authority, and returned decisions but
cannot approve work on the user's behalf. Provider secrets must never be pasted
into chat, command arguments, research records, or tool input.
""".strip()

mcp = FastMCP("Universal Research", instructions=INSTRUCTIONS)


def _startup_reporter(enabled: bool):
    """Return a stderr-only startup reporter that cannot corrupt MCP stdio."""

    def report(percent: int, message: str) -> None:
        if enabled:
            print(
                f"Universal Research MCP startup [{percent:3d}%] {message}",
                file=sys.stderr,
                flush=True,
            )

    return report


def configure_runtime(
    root: Path | str | None = None,
    lexical_db: Path | str | None = None,
    events_root: Path | str | None = None,
) -> None:
    """Set local, project-scoped paths before launching the transport."""

    global ROOT, RESEARCH_DB, EVENTS_ROOT
    ROOT = Path(root).resolve() if root is not None else _default_root()
    RESEARCH_DB = Path(lexical_db).resolve() if lexical_db is not None else Path(
        os.environ.get("UNIVERSAL_RESEARCH_LEXICAL_DB", ROOT / "data/index/research.sqlite")
    ).resolve()
    EVENTS_ROOT = Path(events_root).resolve() if events_root is not None else Path(
        os.environ.get("UNIVERSAL_RESEARCH_EVENTS_ROOT", ROOT / "data/events")
    ).resolve()


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError(f"derived lexical index not found: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _require_current_lexical_index() -> None:
    """Refuse candidate/evidence reads from a stale derived view."""

    from universal_research_mcp.indexing import index_status

    status = index_status(ROOT)
    if status.get("status") != "current":
        raise RuntimeError(
            "derived lexical index is stale; run `universal-research index ensure "
            "--kind lexical --root <project-root>` before retrieval"
        )


def resolve_safe_path(relative_path: str) -> Path:
    supplied = Path(relative_path)
    if supplied.is_absolute() or ".." in supplied.parts:
        raise ValueError("unsafe path")
    lowered = [part.lower() for part in supplied.parts]
    if supplied.name.lower() in DENIED_BASENAMES or any(fragment in part for part in lowered for fragment in DENIED_FRAGMENTS):
        raise ValueError("access to this path is denied")
    resolved = (ROOT / supplied).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("path escapes project root") from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"source artifact not found: {relative_path}")
    return resolved


def _result(row: sqlite3.Row, rank: int, score: float | None = None) -> dict[str, Any]:
    return {
        "rank": rank, "candidate_only": True, "event_id": row["event_id"],
        "event_type": row["event_type"], "status": row["status"], "date": row["date"],
        "summary": row["summary"], "path": row["source_path"],
        "heading": row["source_heading"], "start_line": row["line_start"],
        "end_line": row["line_end"], "source_sha256": row["source_sha256"],
        "lexical_score": score,
    }


def search_lexical(query: str, top_k: int, status: str | None = None) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = [safe_fts_query(query)]
    if status:
        filters.append("e.status = ?")
        params.append(status)
    params.append(top_k)
    where = " AND " + " AND ".join(filters) if filters else ""
    with closing(open_readonly(RESEARCH_DB)) as db:
        passage_rows = db.execute(
            f"""
            SELECT e.event_id, e.event_type, e.status, e.date, e.summary,
                   source_passage_fts.source_path, '' AS source_heading,
                   source_passage_fts.line_start, source_passage_fts.line_end,
                   source_passage_fts.source_sha256, bm25(source_passage_fts) AS bm25_raw
            FROM source_passage_fts JOIN events AS e ON e.event_id = source_passage_fts.event_id
            WHERE source_passage_fts MATCH ? {where}
            ORDER BY bm25_raw ASC LIMIT ?
            """, params,
        ).fetchall()
        rows = db.execute(
            f"""
            SELECT e.event_id, e.event_type, e.status, e.date, e.summary,
                   e.source_path, e.source_heading, e.line_start, e.line_end,
                   e.source_sha256, bm25(event_fts) AS bm25_raw
            FROM event_fts JOIN events e ON e.event_id = event_fts.event_id
            WHERE event_fts MATCH ? {where}
            ORDER BY bm25_raw ASC LIMIT ?
            """, params,
        ).fetchall()
    ordered = [*passage_rows, *rows]
    unique: list[sqlite3.Row] = []
    seen: set[tuple[Any, ...]] = set()
    for row in ordered:
        key = (row["event_id"], row["source_path"], row["line_start"], row["line_end"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
        if len(unique) == top_k:
            break
    return [_result(row, rank, -float(row["bm25_raw"])) for rank, row in enumerate(unique, 1)]


def indexed_source_hashes(path: str, event_id: str | None = None) -> list[str]:
    """Return registered nonempty hashes for one source path or exact event."""

    with closing(open_readonly(RESEARCH_DB)) as db:
        if event_id is not None:
            rows = db.execute(
                """
                SELECT DISTINCT source_sha256 FROM events
                WHERE event_id = ? AND source_path = ?
                  AND source_sha256 IS NOT NULL AND source_sha256 <> ''
                """,
                (event_id, path),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT source_sha256 FROM events
                WHERE source_path = ? AND source_sha256 IS NOT NULL AND source_sha256 <> ''
                UNION
                SELECT source_sha256 FROM sources
                WHERE source_path = ? AND source_sha256 IS NOT NULL AND source_sha256 <> ''
                """,
                (path, path),
            ).fetchall()
    return sorted(str(row["source_sha256"]) for row in rows)


def _recency_key(row: sqlite3.Row) -> float:
    value = row["date"]
    try:
        value = json.loads(row["raw_json"] or "{}").get("timestamp_end") or json.loads(row["raw_json"] or "{}").get("timestamp_start") or value
    except json.JSONDecodeError:
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


@mcp.tool()
def memory_search_candidates(
    query: str,
    top_k: int = 8,
    mode: Literal["lexical"] = "lexical",
    status: str | None = None,
) -> dict[str, Any]:
    """Return lexical search candidates. Fetch original evidence before concluding."""

    if mode != "lexical":
        raise ValueError("query-time semantic and hybrid retrieval are not exposed by this MCP")
    _require_current_lexical_index()
    top_k = max(1, min(int(top_k), 100))
    return {"query": query, "mode": "lexical", "candidate_only": True, "results": search_lexical(query, top_k, status)}


@mcp.tool()
def memory_latest(top_k: int = 5) -> dict[str, Any]:
    """Return latest non-reference records, ordered by recorded event time."""

    _require_current_lexical_index()
    with closing(open_readonly(RESEARCH_DB)) as db:
        rows = db.execute("SELECT event_id, event_type, status, date, summary, raw_json FROM events WHERE event_type <> 'reference_document'").fetchall()
    ordered = sorted(rows, key=lambda row: (_recency_key(row), row["event_id"]), reverse=True)[: max(1, min(int(top_k), 100))]
    return {"results": [{key: row[key] for key in ("event_id", "event_type", "status", "date", "summary")} for row in ordered]}


@mcp.tool()
def memory_fetch_evidence(
    path: str,
    start_line: int,
    end_line: int | None = None,
    context_lines: int = 8,
    event_id: str | None = None,
    expected_sha256: str | None = None,
    allow_mismatched_content: bool = False,
) -> dict[str, Any]:
    """Fetch registered evidence and verify the exact candidate hash when supplied."""

    _require_current_lexical_index()
    hashes = indexed_source_hashes(path, event_id)
    if not hashes:
        qualifier = f" for event {event_id}" if event_id else ""
        raise ValueError(f"source path is not registered in the derived index{qualifier}")
    if expected_sha256 is not None and expected_sha256 not in hashes:
        raise ValueError("expected_sha256 is not registered for this source candidate")
    if expected_sha256 is None:
        if len(hashes) != 1:
            raise ValueError("multiple indexed revisions exist; event_id or expected_sha256 is required")
        expected_sha256 = hashes[0]
    resolved = resolve_safe_path(path)
    start = max(1, int(start_line) - max(0, min(int(context_lines), 50)))
    requested_end = int(end_line) if end_line is not None else int(start_line) + 40
    end = requested_end + max(0, min(int(context_lines), 50))
    if end < start or end - start + 1 > MAX_FETCH_LINES:
        raise ValueError("invalid or excessive fetch range")
    # Read once so returned evidence and its hash describe the same file
    # snapshot even if an external process replaces the artifact immediately
    # afterwards.
    snapshot = resolved.read_bytes()
    lines = snapshot.decode("utf-8", errors="replace").splitlines()
    end = min(end, len(lines))
    content = "\n".join(f"{number}: {text}" for number, text in enumerate(lines[start - 1:end], start))
    current_sha256 = hashlib.sha256(snapshot).hexdigest()
    integrity_status = "matched" if expected_sha256 == current_sha256 else "mismatched"
    result = {
        "event_id": event_id,
        "path": str(resolved.relative_to(ROOT)),
        "start_line": start,
        "end_line": end,
        "sha256": current_sha256,
        "indexed_sha256": expected_sha256,
        "expected_sha256": expected_sha256,
        "current_sha256": current_sha256,
        "integrity_status": integrity_status,
        "content_withheld": integrity_status == "mismatched" and not allow_mismatched_content,
        # Deliberately separate from ``sha256`` (the current file snapshot).
        # This is the exact object accepted by memory_gate_claim, so an agent
        # cannot accidentally turn a mismatched current hash into a claim-gate
        # reference after a successful evidence fetch.
        "claim_gate_reference": {
            "event_id": event_id,
            "path": str(resolved.relative_to(ROOT)),
            "start_line": start,
            "end_line": end,
            "expected_sha256": expected_sha256,
        },
    }
    if integrity_status == "matched" or allow_mismatched_content:
        result["content"] = content
    if integrity_status == "mismatched" and allow_mismatched_content:
        result["diagnostic_mode"] = True
    return result


def _claim_evidence_check(reference: Any) -> dict[str, Any]:
    """Resolve one model-supplied reference without returning source content."""

    if not isinstance(reference, dict):
        return {"verified": False, "reason": "evidence reference must be an object"}
    allowed = {"event_id", "path", "start_line", "end_line", "expected_sha256"}
    unexpected = sorted(set(reference) - allowed)
    if unexpected:
        return {"verified": False, "reason": f"unsupported evidence fields: {', '.join(unexpected)}"}
    event_id = reference.get("event_id")
    path = reference.get("path")
    start_line = reference.get("start_line")
    end_line = reference.get("end_line")
    expected_sha256 = reference.get("expected_sha256")
    if not isinstance(event_id, str) or not event_id:
        return {"verified": False, "reason": "event_id is required"}
    if not isinstance(path, str) or not path:
        return {"verified": False, "reason": "path is required"}
    if not isinstance(start_line, int) or start_line < 1:
        return {"verified": False, "reason": "start_line must be a positive integer"}
    if not isinstance(end_line, int) or end_line < start_line:
        return {"verified": False, "reason": "end_line must be no smaller than start_line"}
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        return {"verified": False, "reason": "expected_sha256 must be a SHA-256 digest"}
    try:
        fetched = memory_fetch_evidence(
            path=path,
            start_line=start_line,
            end_line=end_line,
            context_lines=0,
            event_id=event_id,
            expected_sha256=expected_sha256,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        return {
            "event_id": event_id,
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "expected_sha256": expected_sha256,
            "verified": False,
            "reason": str(exc),
        }
    verified = fetched.get("integrity_status") == "matched"
    return {
        "event_id": event_id,
        "path": path,
        "start_line": fetched.get("start_line"),
        "end_line": fetched.get("end_line"),
        "expected_sha256": expected_sha256,
        "current_sha256": fetched.get("current_sha256"),
        "integrity_status": fetched.get("integrity_status"),
        "verified": verified,
        "reason": "" if verified else "source content hash does not match the registered revision",
    }


@mcp.tool()
def memory_gate_claim(
    claim: str,
    claim_type: Literal[
        "factual", "result", "comparative", "causal", "release",
        "recommendation", "creative",
    ] = "factual",
    materiality: Literal["auto", "routine", "material"] = "auto",
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fail closed when a material claim lacks current, exact source evidence.

    Candidate search output alone never satisfies this gate.  Each supplied
    reference is re-fetched against its registered event ID and source hash;
    the gate returns no source content, only the eligibility receipt.
    """

    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("claim must be a non-empty string")
    checks = [_claim_evidence_check(item) for item in (evidence or [])]
    result = evaluate_claim_gate(
        claim_type=claim_type,
        materiality=materiality,
        evidence_checks=checks,
    )
    return {
        **result,
        "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
        "claim_text_included": False,
    }


@mcp.tool()
def memory_audit_ledger() -> dict[str, Any]:
    """Return read-only policy and record-integrity findings for canonical JSONL."""

    records = [record for event_path in sorted((EVENTS_ROOT / "daily").glob("*/events.jsonl")) for record in read_jsonl(event_path)]
    return audit_report(records)


def research_search(
    query: str,
    top_k: int = 8,
    mode: Literal["lexical"] = "lexical",
    status: str | None = None,
) -> dict[str, Any]:
    """Compatibility alias for ``memory_search_candidates``."""

    return memory_search_candidates(query=query, top_k=top_k, mode=mode, status=status)


def research_latest(top_k: int = 5) -> dict[str, Any]:
    """Compatibility alias for ``memory_latest``."""

    return memory_latest(top_k=top_k)


def research_fetch(
    path: str,
    start_line: int,
    end_line: int | None = None,
    context_lines: int = 8,
    event_id: str | None = None,
    expected_sha256: str | None = None,
    allow_mismatched_content: bool = False,
) -> dict[str, Any]:
    """Compatibility alias for ``memory_fetch_evidence``."""

    return memory_fetch_evidence(
        path=path,
        start_line=start_line,
        end_line=end_line,
        context_lines=context_lines,
        event_id=event_id,
        expected_sha256=expected_sha256,
        allow_mismatched_content=allow_mismatched_content,
    )


_LEGACY_TOOLS_REGISTERED = False


def _register_legacy_tools() -> None:
    """Expose pre-0.2 aliases only when a host explicitly requests them."""

    global _LEGACY_TOOLS_REGISTERED
    if _LEGACY_TOOLS_REGISTERED:
        return
    for function in (research_search, research_latest, research_fetch):
        mcp.tool()(function)
    _LEGACY_TOOLS_REGISTERED = True


@mcp.tool()
def governance_get_capabilities() -> dict[str, Any]:
    """Return the fixed governance roster and non-executing host contract."""

    return {
        "version": GOVERNANCE_VERSION,
        "modes": ["lightweight", "benchmark", "final_review"],
        "critical_review_batch_size": 4,
        "read_only_governance_surface": True,
        "prompt_registry": prompt_registry_report(),
        **registry_report(),
    }


@mcp.tool()
def governance_get_role_manifest(agent_id: str) -> dict[str, Any]:
    """Return one immutable registered role manifest and its canonical hash."""

    manifest = load_registry().get(agent_id)
    if manifest is None:
        raise ValueError("unknown governance agent")
    return {"manifest": manifest, "manifest_hash": manifest_hash(manifest)}


@mcp.tool()
def governance_get_role_prompt_contract(agent_id: str) -> dict[str, Any]:
    """Return the internal versioned prompt contract for one registered role."""

    return load_prompt_pack(agent_id)


@mcp.tool()
def governance_validate_task_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate a proposed agent task without dispatching or approving it."""

    issues = validate_task_packet(packet)
    return {"valid": not issues, "issues": issues}


@mcp.tool()
def governance_validate_decision(decision: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Validate a returned decision against its exact task packet."""

    issues = validate_decision(decision, packet)
    return {"valid": not issues, "issues": issues}


@mcp.tool()
def governance_evaluate_gate(decisions: list[dict[str, Any]], claim_type: str = "publication") -> dict[str, Any]:
    """Evaluate deterministic claim gates; this never grants user approval."""

    return evaluate_gate(decisions, claim_type)


@mcp.tool()
def governance_prepare_codex_dispatch(
    packet: dict[str, Any],
    governor_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare, but do not execute, a host-owned agent dispatch."""

    return build_dispatch_request(packet, governor_receipt)


@mcp.tool()
def governance_prepare_scope_governor_receipt(
    governor_packet: dict[str, Any],
    governor_decision: dict[str, Any],
    governed_packets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind a validated passing scope decision to exact governed task hashes."""

    captured = capture_decision(governor_packet, governor_decision)
    return build_scope_governor_receipt(governor_packet, captured, governed_packets)


@mcp.tool()
def governance_prepare_codex_critical_batch(
    packets: list[dict[str, Any]],
    governor_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare the isolated fixed four-reviewer critical batch."""

    return build_critical_review_batch(packets, governor_receipt)


@mcp.tool()
def governance_capture_codex_decision(packet: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Validate a host decision without writing it to the canonical ledger."""

    return capture_decision(packet, decision)


@mcp.tool()
def governance_assess_plan(operation: dict[str, Any]) -> dict[str, Any]:
    """Assess necessity, bounded work, elapsed time, difficulty, and cost evidence."""

    return assess_plan_necessity(operation)


@mcp.tool()
def governance_evaluate_operation(operation: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Evaluate declarative preflight; never authorize or execute a host tool call."""

    return operation_gate(operation, packet)


@mcp.tool()
def governance_resolve_failure_policy(
    task: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve failure handling without accepting an off/unrecorded mode."""

    return resolve_failure_policy(task=task, profile=profile)


@mcp.tool()
def governance_prepare_failure_record(
    failure: dict[str, Any],
    task: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare the mandatory minimum tombstone; storage remains host-owned."""

    policy = resolve_failure_policy(task=task, profile=profile)
    return build_failure_record(failure, policy)


@mcp.tool()
def research_index_status() -> dict[str, Any]:
    """Report derived-index health without modifying canonical research records."""

    try:
        from universal_research_mcp.indexing import index_status, semantic_status

        status = {"lexical": index_status(ROOT), "semantic": semantic_status(ROOT)}
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        status = {"status": "unavailable", "reason": str(exc)}
    return {"startup": INDEX_STARTUP_STATUS, "current": status}


@mcp.tool()
def governance_preflight_parallel_batch(
    packets: list[dict[str, Any]],
    max_workers: int,
    aggregate_cost_ceiling_usd: float,
    declared_costs_usd: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Validate a parallel batch without starting agents, models, or network."""

    from universal_research_mcp.harness import ParallelResearchHarness

    return ParallelResearchHarness(lambda _dispatch: {}).preflight(
        packets,
        max_workers=max_workers,
        aggregate_cost_ceiling_usd=aggregate_cost_ceiling_usd,
        declared_costs_usd=declared_costs_usd,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the unified Universal Research MCP server.")
    parser.add_argument("--root", type=Path, help="Research project root (default: $UNIVERSAL_RESEARCH_ROOT or current directory).")
    parser.add_argument("--lexical-db", type=Path, help="Derived lexical SQLite path (default: <root>/data/index/research.sqlite).")
    parser.add_argument("--events-root", type=Path, help="Canonical event directory (default: <root>/data/events).")
    parser.add_argument(
        "--auto-index",
        action="store_true",
        help="Create or refresh the local lexical derived index before serving.",
    )
    parser.add_argument(
        "--startup-progress",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Show stderr-only server startup phases. Defaults to enabled in an "
            "interactive terminal and disabled for non-interactive MCP hosts."
        ),
    )
    parser.add_argument(
        "--legacy-tools",
        action="store_true",
        help="Expose the deprecated research_* compatibility tool aliases.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    global INDEX_STARTUP_STATUS
    args = parse_args(argv)
    show_progress = sys.stderr.isatty() if args.startup_progress is None else args.startup_progress
    report = _startup_reporter(show_progress)
    report(5, "resolving the research workspace")
    configure_runtime(args.root, args.lexical_db, args.events_root)
    report(15, f"workspace ready: {ROOT}")
    if args.legacy_tools or os.environ.get("UNIVERSAL_RESEARCH_ENABLE_LEGACY_TOOLS") == "1":
        _register_legacy_tools()
    if args.auto_index:
        try:
            from universal_research_mcp.indexing import ensure_lexical_index

            INDEX_STARTUP_STATUS = ensure_lexical_index(ROOT, progress=report)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            # Keep the MCP available for diagnostics and canonical-ledger audit.
            # The index manager is responsible for preserving the previous good
            # database and recording partial/stale health when a rebuild fails.
            INDEX_STARTUP_STATUS = {"status": "partial", "reason": str(exc)}
            report(100, "lexical index needs repair; server is starting in diagnostic mode")
    else:
        report(75, "automatic lexical-index refresh is disabled")
    report(100, "ready for MCP requests")
    mcp.run()
    return 0
