#!/usr/bin/env python3
"""Deterministic adversarial audit of governance contracts and ingest
receipts (protocol: benchmarks/protocols/adversarial-governance-ingest-v1.md).

Zero model tokens. Each threat sends N hostile inputs to the shipped tools
and requires fail-closed on 100%; a legitimate control set must still pass.
Reuses the repository's own test fixture builders so the inputs are exactly
what the tools accept, mutated adversarially.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import pytest  # noqa: F401  (fixtures import cleanly without it, but keep parity)
from universal_research_mcp.governance.scope_policy import operation_gate, validate_operation_scope
from universal_research_mcp.governance.hashing import artifact_hash

# Reuse the packet builder and ingest fixtures from the test suite unchanged.
import test_governance_v2 as gov
import test_mcp_ingest as ing
from universal_research_mcp import server
from universal_research_mcp.runtime.ingest_approval import IngestApprovalStore
from universal_research_mcp.core.input import all_records
from universal_research_mcp.indexing.lexical import ProjectPaths


def _blocked(gate: dict) -> bool:
    return gate.get("preflight_passed") is False


def _scoped_packet(**kw):
    return gov.packet(**kw)


def audit_governance() -> dict:
    results: dict[str, list[bool]] = {}

    # T1 privilege/scope escape
    t1 = []
    base = _scoped_packet()
    sh = base["authority"]["scope_hash"]
    for op in [
        {"action": "delete_canonical", "paths": ["docs"], "scope_hash": sh},
        {"action": "inspect_artifact", "paths": ["/etc/passwd"], "scope_hash": sh},
        {"action": "inspect_artifact", "paths": ["docs"], "network": True, "scope_hash": sh},
        {"action": "inspect_artifact", "paths": ["docs"], "model_execution": True, "scope_hash": sh},
        {"action": "inspect_artifact", "paths": ["docs"], "__exec__": "run", "scope_hash": sh},
        {"action": "inspect_artifact", "paths": ["docs"], "scope_hash": "sha256:" + "0" * 64},
    ]:
        try:
            t1.append(_blocked(operation_gate(op, base)))
        except Exception:
            t1.append(True)
    results["T1_scope_escape"] = t1

    # T2 cost / plan evasion
    t2 = []
    over = _scoped_packet()
    sh2 = over["authority"]["scope_hash"]
    for op in [
        {"action": "run_benchmark", "paths": ["docs"], "estimated_cost_usd": 999.0, "scope_hash": sh2},
        {"action": "run_benchmark", "paths": ["docs"], "network": True, "parallelism": 8, "scope_hash": sh2},
        {"action": "inspect_artifact", "paths": ["docs"], "estimated_cost_usd": -1.0, "scope_hash": sh2},
    ]:
        try:
            t2.append(_blocked(operation_gate(op, over)))
        except Exception:
            t2.append(True)
    results["T2_cost_plan_evasion"] = t2

    # T7 authority poisoning (non-hashable / nested unknown)
    t7 = []
    for bad in [
        {"max_cost_usd": float("nan")},
        {"timeout_seconds": float("inf")},
        {"nested": {"deep": {"weird": float("-inf")}}},
    ]:
        try:
            artifact_hash(bad)
            t7.append(False)  # should have raised
        except Exception:
            t7.append(True)
    # malformed packet must fail closed, not crash-leak
    for bad_packet in [None, {}, {"scope": "not-an-object"}, {"scope": {}, "authority": 5}]:
        try:
            t7.append(_blocked(operation_gate({"action": "inspect_artifact", "paths": ["docs"]}, bad_packet)))
        except Exception:
            t7.append(True)
    results["T7_authority_poisoning"] = t7

    # Legitimate control: a well-formed in-scope inspect must pass preflight
    legit = _scoped_packet()
    good_op = {"action": "inspect_artifact", "paths": ["docs"], "scope_hash": legit["authority"]["scope_hash"]}
    results["_legit_pass"] = [operation_gate(good_op, legit).get("preflight_passed") is True]
    return results


def audit_ingest() -> dict:
    results: dict[str, list[bool]] = {}
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    tmp = Path(tempfile.mkdtemp())
    try:
        import os
        state_root = tmp / "host-state"
        os.environ["UNIVERSAL_RESEARCH_INGEST_APPROVAL_STATE_ROOT"] = str(state_root)
        root = tmp / "research"
        source, record = ing._prepared_project(root)
        server.configure_runtime(root)
        prepared = server.research_prepare_ingest(
            record, "approval_ingest",
            [{"path": "docs/ingest.md", "source_id": "src_ingest", "source_type": "markdown"}],
        )
        did = prepared["draft_id"]; dsha = prepared["draft_sha256"]

        # T3 receipt forgery
        t3 = []
        for rid in ["forged_receipt", "", "receipt_" + "0" * 40, did]:
            try:
                server.research_commit_ingest(did, dsha, rid)
                t3.append(False)
            except Exception:
                t3.append(True)
        results["T3_receipt_forgery"] = t3

        # issue one real receipt for the remaining tests
        receipt = ing._receipt(root, prepared, state_root)

        # T5 draft tampering: wrong draft_sha256 with a valid receipt
        t5 = []
        for bad_sha in ["0" * 64, dsha[:-1] + ("0" if dsha[-1] != "0" else "1")]:
            try:
                server.research_commit_ingest(did, bad_sha, receipt["receipt_id"])
                t5.append(False)
            except Exception:
                t5.append(True)
        results["T5_draft_tampering"] = t5

        # legitimate commit must succeed exactly once
        committed = server.research_commit_ingest(did, dsha, receipt["receipt_id"])
        results["_legit_commit"] = [committed.get("status") == "committed"]

        # T4 replay: reuse the now-spent receipt
        t4 = []
        try:
            server.research_commit_ingest(did, dsha, receipt["receipt_id"])
            t4.append(False)
        except Exception:
            t4.append(True)
        results["T4_replay"] = t4

        # T6 approval bypass: prepare with a non-existent approval
        t6 = []
        try:
            server.research_prepare_ingest(record, "approval_does_not_exist")
            t6.append(False)
        except Exception:
            t6.append(True)
        # commit-time forged approval already covered by T3; add a scope-mismatch prepare
        bad_record = copy.deepcopy(record)
        bad_record["record_id"] = "observation_out_of_scope"
        bad_record["study_id"] = "study_unapproved"
        try:
            server.research_prepare_ingest(bad_record, "approval_ingest")
            t6.append(False)
        except Exception:
            t6.append(True)
        results["T6_approval_bypass"] = t6
    finally:
        server.configure_runtime(*prior)
    return results


def main() -> int:
    gov_r = audit_governance()
    ing_r = audit_ingest()
    all_r = {**gov_r, **ing_r}
    threats = {k: v for k, v in all_r.items() if not k.startswith("_")}
    controls = {k: v for k, v in all_r.items() if k.startswith("_")}
    summary = {
        "threats": {k: {"fail_closed": sum(v), "total": len(v),
                        "all_fail_closed": all(v)} for k, v in threats.items()},
        "controls": {k: {"passed": sum(v), "total": len(v), "all_pass": all(v)} for k, v in controls.items()},
        "aggregate_threat_cases": sum(len(v) for v in threats.values()),
        "aggregate_fail_closed": sum(sum(v) for v in threats.values()),
        "every_threat_fully_fail_closed": all(all(v) for v in threats.values()),
        "every_control_passes": all(all(v) for v in controls.values()),
    }
    out = REPO / "benchmarks/adversarial/audit-results.json"
    out.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
