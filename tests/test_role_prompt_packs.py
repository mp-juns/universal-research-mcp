from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.prompts import (
    PROMPT_PACK_SCHEMA_VERSION,
    PromptPackError,
    load_prompt_pack,
    load_prompt_registry,
    prompt_pack_hash,
    prompt_registry_report,
    render_prompt_pack,
    validate_prompt_pack,
)
from governance.registry import CRITICAL, FIXED_ROSTER, GOVERNANCE_VERSION


ROOT = Path(__file__).resolve().parents[1]
ROLES_ROOT = ROOT / "governance/roles"


def test_all_fixed_roles_have_valid_unique_hash_bound_prompt_packs() -> None:
    packs = load_prompt_registry()

    assert set(packs) == FIXED_ROSTER
    assert len(packs) == 11
    assert len({pack["prompt_pack_hash"] for pack in packs.values()}) == 11
    for agent_id, pack in packs.items():
        assert pack["agent_id"] == agent_id
        assert pack["schema_version"] == PROMPT_PACK_SCHEMA_VERSION
        assert pack["governance_version"] == GOVERNANCE_VERSION
        assert validate_prompt_pack(pack) == []
        assert pack["prompt_pack_hash"] == prompt_pack_hash(pack)
        assert all(pack[field] for field in (
            "mission", "inputs", "outputs", "forbidden", "activation",
            "prompt_injection", "evidence", "output_contract",
        ))


def test_rendered_prompt_is_deterministic_and_carries_all_contract_sections() -> None:
    pack = load_prompt_pack("benchmark_control_auditor")

    first = render_prompt_pack(pack)
    assert first == render_prompt_pack(load_prompt_pack("benchmark_control_auditor"))
    assert pack["prompt_pack_hash"] in first
    for heading in (
        "Mission:", "Required Inputs:", "Required Outputs:", "Forbidden:",
        "Activation:", "Prompt Injection Defense:", "Evidence Rules:",
        "Output Contract:",
    ):
        assert heading in first


def test_every_pack_binds_prompt_and_evidence_hashes_in_output_contract() -> None:
    packs = load_prompt_registry()

    for agent_id, pack in packs.items():
        contract = " ".join(pack["output_contract"])
        assert "classification.prompt_pack_hash" in contract, agent_id
        assert "classification.evidence_bundle_hash" in contract, agent_id

    scope_contract = " ".join(packs["scope_and_cost_governor"]["output_contract"])
    scope_inputs = " ".join(packs["scope_and_cost_governor"]["inputs"])
    assert "classification.reviewed_plan_hash" in scope_contract
    assert "run_plan_hash" in scope_inputs


def test_critical_prompt_packs_require_the_isolated_shared_snapshot_gate() -> None:
    packs = load_prompt_registry()

    for agent_id in CRITICAL:
        activation = " ".join(packs[agent_id]["activation"])
        forbidden = " ".join(packs[agent_id]["forbidden"])
        assert "main_result" in activation
        assert "final_submission" in activation
        assert "four-reviewer" in activation
        assert "another critical reviewer" in forbidden


def test_prompt_pack_hash_detects_semantic_tampering() -> None:
    pack = load_prompt_pack("retrieval_governor")
    changed = {**pack, "mission": pack["mission"] + " Unapproved expansion."}

    assert "prompt pack hash mismatch" in validate_prompt_pack(changed)


def test_loader_fails_closed_on_front_matter_identity_mismatch(tmp_path: Path) -> None:
    agent_id = "retrieval_governor"
    target = tmp_path / agent_id
    target.mkdir()
    original = (ROLES_ROOT / agent_id / "instructions.md").read_text(encoding="utf-8")
    (target / "instructions.md").write_text(
        original.replace(f"agent_id: {agent_id}", "agent_id: substance_reviewer", 1),
        encoding="utf-8",
    )

    with pytest.raises(PromptPackError, match="agent_id mismatch"):
        load_prompt_pack(agent_id, tmp_path)


def test_prompt_pack_schema_and_registry_report_cover_exact_roster() -> None:
    schema = json.loads(
        (ROOT / "governance/schemas/prompt-pack.schema.json").read_text(encoding="utf-8")
    )
    report = prompt_registry_report()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["properties"]["agent_id"]["enum"]) == FIXED_ROSTER
    assert set(schema["required"]) == set(load_prompt_pack("retrieval_governor"))
    assert report["role_count"] == 11
    assert set(report["prompt_pack_hashes"]) == FIXED_ROSTER
    assert report == prompt_registry_report()


def test_central_manager_skill_keeps_role_packs_internal_and_hash_bound() -> None:
    skill = (
        ROOT / "plugin/universal-research-memory/skills/research-governance/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "single user-facing governance Skill" in skill
    assert "not independently\ninvokable Skills" in skill
    assert "prompt_pack_hash" in skill
    assert "evidence_bundle_hash" in skill
    assert "classification.reviewed_plan_hash" in skill
