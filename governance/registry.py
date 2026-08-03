"""Manifest-backed fixed roster registry with no dynamic role creation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from governance.hashing import artifact_hash


GOVERNANCE_VERSION = "agent-governance/2.0"
SCOPE_AND_COST_GOVERNOR = "scope_and_cost_governor"

OPERATIONAL = frozenset({
    "retrieval_governor", "benchmark_control_auditor", "analysis_objectivity_auditor",
    "paper_evidence_evaluator", "correction_executor", "research_memory_maintainer",
    SCOPE_AND_COST_GOVERNOR,
})
CRITICAL = frozenset({
    "cold_adversarial_reviewer", "substance_reviewer", "user_alignment_reviewer",
    "reproducibility_reviewer",
})
FIXED_ROSTER = OPERATIONAL | CRITICAL
VALID_ACTIONS = frozenset({
    "research_search", "research_fetch", "inspect_artifact", "emit_finding",
    "emit_decision", "compare_manifest", "propose_correction", "edit_derived_artifact",
    "rebuild_derived_index", "repair_index", "verify_correction",
    "assess_plan_necessity", "estimate_operation", "validate_operation_scope",
    "request_user_decision",
})


def _roles_root() -> Path:
    return Path(__file__).resolve().parent / "roles"


def load_registry(roles_root: Path | None = None) -> dict[str, dict[str, Any]]:
    root = roles_root or _roles_root()
    manifests: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*/role.yaml")):
        # JSON is valid YAML 1.2, which keeps the core dependency-free.
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifests[str(manifest.get("agent_id"))] = manifest
    return manifests


def manifest_hash(manifest: dict[str, Any]) -> str:
    return artifact_hash(manifest)


def registry_hash(registry: dict[str, dict[str, Any]] | None = None) -> str:
    registry = registry or load_registry()
    return artifact_hash({agent_id: manifest_hash(manifest) for agent_id, manifest in sorted(registry.items())})


def validate_registry(registry: dict[str, dict[str, Any]] | None = None) -> list[dict[str, str]]:
    registry = registry or load_registry()
    issues: list[dict[str, str]] = []
    found = set(registry)
    if found != FIXED_ROSTER:
        issues.append({"code": "GOV-REGISTRY-002", "message": "role registry must contain exactly the fixed eleven-role roster"})
    for agent_id, manifest in registry.items():
        if manifest.get("schema_version") != GOVERNANCE_VERSION:
            issues.append({"code": "GOV-REGISTRY-002", "message": f"{agent_id} has an unsupported role schema"})
        expected_type = "critical" if agent_id in CRITICAL else "operational"
        if manifest.get("role_type") != expected_type:
            issues.append({"code": "GOV-REGISTRY-002", "message": f"{agent_id} has an invalid role type"})
        authority = manifest.get("authority") or {}
        allowed = authority.get("allowed_actions")
        forbidden = authority.get("forbidden_actions")
        if not isinstance(allowed, list) or not set(allowed) <= VALID_ACTIONS:
            issues.append({"code": "GOV-REGISTRY-002", "message": f"{agent_id} declares an invalid action"})
        if not isinstance(forbidden, list):
            issues.append({"code": "GOV-REGISTRY-002", "message": f"{agent_id} has no forbidden-action contract"})
        if agent_id in CRITICAL and set(allowed or []) & {"edit_derived_artifact", "rebuild_derived_index", "repair_index"}:
            issues.append({"code": "GOV-REGISTRY-002", "message": f"critical reviewer {agent_id} has write authority"})
        if agent_id == "correction_executor" and "verify_correction" in set(allowed or []):
            issues.append({"code": "GOV-REGISTRY-002", "message": "correction executor cannot verify-close its own correction"})
        if agent_id == SCOPE_AND_COST_GOVERNOR:
            modes = set((manifest.get("activation") or {}).get("modes") or [])
            if modes != {"lightweight", "benchmark", "final_review"}:
                issues.append({"code": "GOV-REGISTRY-002", "message": "scope and cost governor must be active in every mode"})
            if set(allowed or []) & {"edit_derived_artifact", "rebuild_derived_index", "repair_index"}:
                issues.append({"code": "GOV-REGISTRY-002", "message": "scope and cost governor cannot execute writes"})
    return issues


def registry_report() -> dict[str, Any]:
    registry = load_registry()
    return {
        "schema_version": "urag-registry-report/2.0",
        "governance_version": GOVERNANCE_VERSION,
        "role_count": len(registry),
        "role_ids": sorted(registry),
        "registry_hash": registry_hash(registry),
        "issues": validate_registry(registry),
    }
