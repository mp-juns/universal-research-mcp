"""Build isolated synthetic source bundles for Integrity & Claim-Gating v1.

The builder creates development fixtures only.  It deliberately never writes
into a reference project and refuses a non-empty destination.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from universal_research_mcp.core.input import append_record, register_source
from universal_research_mcp.indexing import ensure_lexical_index, index_status, initialize_project


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_id(value: str) -> str:
    return value.replace(".", "_").replace("-", "_")


def _approval(task_id: str) -> dict[str, Any]:
    safe = _safe_id(task_id)
    return {
        "schema_version": "core/1.0", "record_id": f"approval_{safe}",
        "record_kind": "approval", "study_id": f"study_{safe}",
        "occurred_at": "2026-08-13T00:00:00+00:00",
        "recorded_at": "2026-08-13T00:00:00+00:00", "status": "approved",
        "created_by": {"actor_id": "actor_fixture_owner", "actor_type": "human"},
        "payload": {"scope": {"study_ids": [f"study_{safe}"], "record_kinds": ["observation"]}},
    }


def _observation(task_id: str, number: int, path: str, digest: str, *, status: str = "completed") -> dict[str, Any]:
    safe = _safe_id(task_id)
    record_id = f"observation_{safe}_{number}"
    return {
        "schema_version": "core/1.0", "record_id": record_id,
        "record_kind": "observation", "study_id": f"study_{safe}",
        "occurred_at": "2026-08-13T00:01:00+00:00",
        "recorded_at": "2026-08-13T00:01:00+00:00", "status": status,
        "created_by": {"actor_id": "actor_fixture_owner", "actor_type": "human"},
        "approval_refs": [f"approval_{safe}"],
        "source_refs": [{
            "artifact_revision_id": f"artifact_{safe}_{number}@sha256:{digest}",
            "locator": {"kind": "line_range", "path": path, "start": 1, "end": 2},
            "verification_status": "integrity_verified",
        }],
        "artifact_refs": [f"artifact_{safe}_{number}"],
        "payload": {"summary": f"Fixture evidence for {task_id} record {number}."},
    }


def _documents(task: Mapping[str, Any]) -> tuple[dict[str, str], set[str]]:
    """Return all files and the explicit source-registry allowlist."""

    state = str(task["evidence_state"])
    claim_type = str(task["claim_type"])
    positive = f"Current synthetic evidence supports the requested {claim_type} claim."
    files = {
        "docs/primary.md": f"Primary evidence\n{positive}\n",
        "docs/corroboration.md": f"Corroboration\nIndependent evidence supports the requested {claim_type} claim.\n",
    }
    registered = set(files)
    if state == "conflicting_evidence":
        files["docs/corroboration.md"] = "Corroboration\nIndependent evidence contradicts the requested claim.\n"
    elif state == "missing_evidence":
        files["docs/primary.md"] = "Primary evidence\nNo record addresses the requested claim.\n"
    elif state == "valid_but_irrelevant":
        files["docs/primary.md"] = "Battery inventory\nThe laboratory has twelve spare battery modules.\n"
        files["docs/corroboration.md"] = "Battery audit\nThe battery count was independently verified.\n"
    elif state == "poisoned_before_registration":
        files["docs/primary.md"] = "Primary evidence\nA poisoned pre-registration source falsely supports the requested claim.\n"
    elif state == "withdrawn_evidence":
        files["docs/primary.md"] = "Withdrawn evidence\nThis historical supporting result is withdrawn and must not support a current conclusion.\n"
    elif state == "superseded_amendment":
        files["docs/primary.md"] = "Superseded interpretation\nThe prior interpretation is retained only as historical context.\n"
        files["docs/corroboration.md"] = "Current amendment\nThe current interpretation replaces the prior interpretation while preserving lineage.\n"
    elif state == "unregistered_source":
        files["docs/unregistered.md"] = f"Unregistered evidence\nThis unregistered file appears to support the requested {claim_type} claim.\n"
        registered = {"docs/primary.md"}
        files["docs/primary.md"] = "Registered context\nNo registered evidence supports the requested claim.\n"
    return files, registered


def _inject_fault(task: Mapping[str, Any], root: Path, approval_ref: str) -> None:
    state = str(task["evidence_state"])
    primary = root / "docs/primary.md"
    if state in {"post_index_mutation", "path_reuse"}:
        primary.write_text("Primary evidence\nChanged after indexing: the requested claim is not supported.\n", encoding="utf-8")
    elif state == "line_range_drift":
        primary.write_text("Inserted line\nInserted line\nPrimary evidence\nThe prior line range no longer identifies this claim.\n", encoding="utf-8")
    elif state == "stale_derived_index":
        digest = _sha256(primary)
        append_record(
            root,
            _observation(str(task["task_id"]), 99, "docs/primary.md", digest),
            approval_ref=approval_ref,
        )


def build_development_fixture(task: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    """Build one self-contained source/index fixture and return its manifest."""

    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"fixture destination must be empty: {destination}")
    initialize_project(destination)
    documents, registered = _documents(task)
    for relative, content in documents.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for number, relative in enumerate(sorted(registered), start=1):
        register_source(destination, relative, source_id=f"src_{_safe_id(str(task['task_id']))}_{number}", source_type="markdown")
    approval = _approval(str(task["task_id"]))
    append_record(destination, approval, approval_bootstrap=True)
    observations: list[dict[str, str]] = []
    for number, relative in enumerate(sorted(registered), start=1):
        digest = _sha256(destination / relative)
        status = "superseded" if task["evidence_state"] == "withdrawn_evidence" else "completed"
        record = _observation(str(task["task_id"]), number, relative, digest, status=status)
        append_record(destination, record, approval_ref=str(approval["record_id"]))
        observations.append({"event_id": str(record["record_id"]), "path": relative, "expected_sha256": digest})
    indexed = ensure_lexical_index(destination)
    _inject_fault(task, destination, str(approval["record_id"]))
    source_hashes = {relative: _sha256(destination / relative) for relative in documents}
    return {
        "schema_version": "integrity-claim-gate-fixture/1.0",
        "task_id": task["task_id"], "root": str(destination),
        "evidence_state": task["evidence_state"],
        "registered_paths": sorted(registered), "all_paths": sorted(documents),
        "evidence_references": observations, "post_setup_source_sha256": source_hashes,
        "index_status": index_status(destination),
        "index_fingerprint": indexed["indexed_fingerprint"],
        "development_only": True,
    }


def build_development_fixtures(tasks: Iterable[Mapping[str, Any]], destination: Path) -> list[dict[str, Any]]:
    """Build all task fixtures and emit a manifest inside the destination."""

    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"fixture destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    manifests = [build_development_fixture(task, destination / str(task["task_id"])) for task in tasks]
    (destination / "fixture-manifest.json").write_text(
        json.dumps({"schema_version": "integrity-claim-gate-fixtures/1.0", "fixtures": manifests}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifests
