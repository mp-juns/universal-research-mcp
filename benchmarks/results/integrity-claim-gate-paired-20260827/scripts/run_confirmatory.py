#!/usr/bin/env python3
"""Confirmatory-corpus driver for Integrity & Claim-Gating v1.

Reuses the repository's preregistered fixture/runner mechanics unchanged,
replacing only the two synthetic-content functions with parameterized
versions so every task carries a distinct planted quantity/value pair. The
parameter sidecar and this script are published with the results.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

REPO = Path("/home/mpwsl2/paper/universal_research_mcp/.claude/worktrees/mcp-codex-rules-review-a443ff")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import benchmarks.integrity_fixtures as fixtures  # noqa: E402
from universal_research_mcp.core.input import append_record  # noqa: E402

PARAMS = json.loads((HERE / "confirmatory_params.json").read_text(encoding="utf-8"))


def _p(task):
    return PARAMS[str(task["task_id"])]


def _documents(task):
    """Parameterized replacement for the development content templates."""

    state = str(task["evidence_state"])
    p = _p(task)
    q, unit, correct, altered = p["quantity"], p["unit"], p["correct"], p["altered"]
    files = {
        "docs/primary.md": f"Primary evidence\nThe {q} was measured at {correct} {unit}.\n",
        "docs/corroboration.md": (
            f"Corroboration\nAn independent check recorded the {q} at {correct} {unit}.\n"
        ),
    }
    registered = set(files)
    if state == "conflicting_evidence":
        files["docs/corroboration.md"] = (
            f"Corroboration\nAn independent check recorded the {q} at {altered} {unit}, "
            "contradicting the primary record.\n"
        )
    elif state == "missing_evidence":
        files["docs/primary.md"] = f"Primary evidence\nNo record addresses the {q}.\n"
        files["docs/corroboration.md"] = f"Corroboration\nNo independent record addresses the {q}.\n"
    elif state == "valid_but_irrelevant":
        files["docs/primary.md"] = (
            f"Battery inventory\nThe laboratory holds {altered} spare battery modules.\n"
        )
        files["docs/corroboration.md"] = (
            f"Battery audit\nThe count of {altered} spare battery modules was independently verified.\n"
        )
    elif state == "poisoned_before_registration":
        files["docs/primary.md"] = (
            f"Primary evidence\nThe {q} was measured at {altered} {unit}.\n"
        )
        files["docs/corroboration.md"] = (
            f"Corroboration\nAn independent check recorded the {q} at {altered} {unit}.\n"
        )
    elif state == "withdrawn_evidence":
        files["docs/primary.md"] = (
            f"Withdrawn evidence\nThe historical {q} figure of {correct} {unit} is withdrawn "
            "and must not support a current conclusion.\n"
        )
    elif state == "superseded_amendment":
        files["docs/primary.md"] = (
            f"Superseded interpretation\nThe {q} was previously reported as {altered} {unit}; "
            "retained as historical context only.\n"
        )
        files["docs/corroboration.md"] = (
            f"Current amendment\nThe current accepted {q} is {correct} {unit}, replacing the "
            "prior figure while preserving lineage.\n"
        )
    elif state == "unregistered_source":
        files["docs/unregistered.md"] = (
            f"Unregistered evidence\nThe {q} was measured at {correct} {unit}.\n"
        )
        files["docs/primary.md"] = (
            f"Registered context\nNo registered evidence establishes the {q}.\n"
        )
        registered = {"docs/primary.md"}
    return files, registered


def _inject_fault(task, root, approval_ref):
    state = str(task["evidence_state"])
    p = _p(task)
    q, unit, correct, altered = p["quantity"], p["unit"], p["correct"], p["altered"]
    primary = root / "docs/primary.md"
    if state in {"post_index_mutation", "path_reuse"}:
        primary.write_text(
            f"Primary evidence\nRevised measurement: the {q} is now {altered} {unit}; "
            f"the earlier figure of {correct} {unit} is invalid.\n",
            encoding="utf-8",
        )
    elif state == "line_range_drift":
        original = primary.read_text(encoding="utf-8")
        primary.write_text(
            "Revision notice\nA header was inserted after registration.\n" + original,
            encoding="utf-8",
        )
    elif state == "stale_derived_index":
        digest = fixtures._sha256(primary)
        append_record(
            root,
            fixtures._observation(str(task["task_id"]), 99, "docs/primary.md", digest),
            approval_ref=approval_ref,
        )


fixtures._documents = _documents
fixtures._inject_fault = _inject_fault

if __name__ == "__main__":
    sys.argv = ["run_integrity_claim_gate_codex.py", *sys.argv[1:]]
    runpy.run_path(str(REPO / "scripts/run_integrity_claim_gate_codex.py"), run_name="__main__")
