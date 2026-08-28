from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from universal_research_mcp import server
from universal_research_mcp.core.input import append_record, register_source
from universal_research_mcp.indexing.lexical import ensure_lexical_index, initialize_project


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approval() -> dict[str, object]:
    return {
        "schema_version": "core/1.0", "record_id": "approval_claim_gate",
        "record_kind": "approval", "study_id": "study_claim_gate",
        "occurred_at": "2026-08-13T00:00:00+00:00",
        "recorded_at": "2026-08-13T00:00:00+00:00", "status": "approved",
        "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
        "payload": {"scope": {"study_ids": ["study_claim_gate"], "record_kinds": ["observation"]}},
    }


def _observation(record_id: str, path: str, digest: str) -> dict[str, object]:
    return {
        "schema_version": "core/1.0", "record_id": record_id,
        "record_kind": "observation", "study_id": "study_claim_gate",
        "occurred_at": "2026-08-13T00:01:00+00:00",
        "recorded_at": "2026-08-13T00:01:00+00:00", "status": "completed",
        "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
        "approval_refs": ["approval_claim_gate"],
        "source_refs": [{
            "artifact_revision_id": f"artifact_{record_id}@sha256:{digest}",
            "locator": {"kind": "line_range", "path": path, "start": 1, "end": 2},
            "verification_status": "integrity_verified",
        }],
        "artifact_refs": [f"artifact_{record_id}"],
        "payload": {"summary": f"Claim gate evidence {record_id}"},
    }


def _reference(record_id: str, path: str, digest: str) -> dict[str, object]:
    return {
        "event_id": record_id, "path": path,
        "start_line": 1, "end_line": 2, "expected_sha256": digest,
    }


def _configured_project(root: Path) -> tuple[Path, list[dict[str, object]]]:
    initialize_project(root)
    documents = {
        "docs/runtime.md": "Runtime contract\nGPU offload is disabled.\nDisplay context only.\nUnrelated conclusion.\n",
        "docs/audit.md": "Audit contract\nJoint packing is required.\n",
    }
    for index, (path, text) in enumerate(documents.items(), start=1):
        source = root / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text, encoding="utf-8")
        register_source(root, path, source_id=f"src_claim_{index}", source_type="markdown")
    append_record(root, _approval(), approval_bootstrap=True)
    references: list[dict[str, object]] = []
    for record_id, path in (("observation_runtime", "docs/runtime.md"), ("observation_audit", "docs/audit.md")):
        digest = _sha256(root / path)
        append_record(root, _observation(record_id, path, digest), approval_ref="approval_claim_gate")
        references.append(_reference(record_id, path, digest))
    ensure_lexical_index(root)
    server.configure_runtime(root)
    return root, references


def test_claim_type_is_required_and_inactive_gate_reports_resolved_evidence(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _root, references = _configured_project(tmp_path / "research")
        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        schema = tools["memory_check_evidence_eligibility"].inputSchema
        assert "claim_type" in schema["required"]

        receipt = server.memory_check_evidence_eligibility(
            "Routine factual lookup.", "factual", "auto", [references[0]],
        )
        assert receipt["status"] == "not_required"
        assert receipt["active"] is False
        # Supplied evidence was resolved and must be reported, not discarded.
        assert receipt["evidence"][0]["verified"] is True
        assert receipt["evidence"][0]["integrity_status"] == "matched"
        assert receipt["blockers"] == []
    finally:
        server.configure_runtime(*prior)


def test_evidence_eligibility_is_exposed_and_requires_two_current_records_for_release(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _root, references = _configured_project(tmp_path / "research")
        tools = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
        assert "memory_check_evidence_eligibility" in tools
        assert "memory_gate_claim" not in tools

        blocked = server.memory_check_evidence_eligibility(
            "The release is ready.", "release", "auto", [references[0]],
        )
        assert blocked["status"] == "blocked"
        assert blocked["blockers"][0]["code"] == "EVIDENCE-ELIGIBILITY-INSUFFICIENT"

        eligible = server.memory_check_evidence_eligibility(
            "The release is ready.", "release", "auto", references,
        )
        assert eligible["status"] == "eligible"
        assert eligible["claim_eligibility"] == "eligible"
        assert eligible["evidence_eligibility"] == "eligible"
        assert eligible["claim_verified"] is False
        assert eligible["semantic_support_checked"] is False
        assert eligible["conflict_checked"] is False
        assert eligible["source_truth_checked"] is False
        assert eligible["claim_text_included"] is False
        assert len(eligible["evidence"]) == 2
        fetched = server.memory_fetch_evidence(**references[0], context_lines=0)
        assert fetched["claim_gate_reference"] == references[0]
    finally:
        server.configure_runtime(*prior)


def test_identity_gate_accepts_source_less_canonical_records_but_not_forged_null_locators(
    tmp_path: Path,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _configured_project(tmp_path / "research")

        result = server.memory_search_candidates(
            "approval_claim_gate", mode="lexical",
        )
        approval = next(
            item for item in result["results"]
            if item["event_id"] == "approval_claim_gate"
        )
        assert result["routing"]["identity_gate"]["status"] == "passed"
        assert approval["canonical_identity_verified"] is True
        assert approval["evidence_eligible"] is False
        # Null locator fields are compacted away; absence means source-less.
        assert "path" not in approval

        evidence = server.memory_search_candidates(
            "Claim gate evidence", mode="lexical", status="completed",
        )["results"][0]
        forged = dict(evidence)
        forged.update({
            "path": None,
            "heading": None,
            "start_line": None,
            "end_line": None,
            "source_sha256": None,
        })
        with pytest.raises(
            RuntimeError, match="candidate locator failed the canonical identity gate",
        ):
            server._apply_candidate_identity_gate([forged])
    finally:
        server.configure_runtime(*prior)


def test_evidence_eligibility_blocks_a_changed_source_and_skips_routine_lookup(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root, references = _configured_project(tmp_path / "research")
        routine = server.memory_check_evidence_eligibility("Where is the runtime file?", "factual", "auto", [])
        assert routine["status"] == "not_required"

        (root / "docs/runtime.md").write_text("Runtime contract\nGPU offload is enabled.\n", encoding="utf-8")
        blocked = server.memory_check_evidence_eligibility(
            "GPU offload is enabled for this release.", "factual", "material", [references[0]],
        )
        assert blocked["status"] == "blocked"
        assert blocked["evidence"][0]["verified"] is False
        assert blocked["claim_eligibility"] == "blocked"
    finally:
        server.configure_runtime(*prior)


def test_evidence_eligibility_is_deterministic_and_rejects_mixed_bindings(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _root, references = _configured_project(tmp_path / "research")
        first = server.memory_check_evidence_eligibility("The release is ready.", "release", "auto", references)
        second = server.memory_check_evidence_eligibility("The release is ready.", "release", "auto", references)
        assert first == second

        mixed = [dict(references[0]), dict(references[1])]
        mixed[0]["path"] = str(references[1]["path"])
        blocked = server.memory_check_evidence_eligibility("The release is ready.", "release", "auto", mixed)
        assert blocked["status"] == "blocked"
        assert any(item["code"] == "EVIDENCE-INTEGRITY-INVALID" for item in blocked["blockers"])
    finally:
        server.configure_runtime(*prior)


@pytest.mark.parametrize("start,end", [(1, 1), (2, 3), (3, 4), (100, 120)])
def test_evidence_rejects_unregistered_ranges_with_the_same_event_path_and_hash(
    tmp_path: Path, start: int, end: int,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _root, references = _configured_project(tmp_path / "research")
        reference = {**references[0], "start_line": start, "end_line": end}
        with pytest.raises(ValueError, match="exact evidence range"):
            server.memory_fetch_evidence(**reference)
        receipt = server.memory_check_evidence_eligibility(
            "The registered evidence supports this result.", "result", "material", [reference],
        )
        assert receipt["status"] == "blocked"
        assert receipt["evidence"][0]["verified"] is False
    finally:
        server.configure_runtime(*prior)


def test_display_context_never_expands_the_canonical_claim_reference(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _root, references = _configured_project(tmp_path / "research")
        for context in (0, 8, 50):
            fetched = server.memory_fetch_evidence(**references[0], context_lines=context)
            assert fetched["claim_gate_reference"] == references[0]
            assert (fetched["start_line"], fetched["end_line"]) == (1, 2)
            assert fetched["context_end_line"] == (2 if context == 0 else 4)
            assert ("Display context only." in fetched["content"]) is (context > 0)
            assert server.memory_check_evidence_eligibility(
                "GPU offload is disabled.", "factual", "material",
                [fetched["claim_gate_reference"]],
            )["status"] == "eligible"
        without_end = {key: value for key, value in references[0].items() if key != "end_line"}
        assert server.memory_fetch_evidence(**without_end)["claim_gate_reference"] == references[0]
    finally:
        server.configure_runtime(*prior)


@pytest.mark.parametrize("start,end", [(0, 2), (True, 2), (1, False), (2, 1), (1, 1.5)])
def test_fetch_rejects_invalid_line_types_and_order(tmp_path: Path, start: object, end: object) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _root, references = _configured_project(tmp_path / "research")
        reference = {**references[0], "start_line": start, "end_line": end}
        with pytest.raises(ValueError):
            server.memory_fetch_evidence(**reference)
        assert server.memory_check_evidence_eligibility(
            "Material result.", "result", "material", [reference],
        )["status"] == "blocked"
    finally:
        server.configure_runtime(*prior)


@pytest.mark.parametrize("start,end", [(1, 5), (100, 120)])
def test_registered_file_diagnostics_reject_ranges_past_eof(
    tmp_path: Path, start: int, end: int,
) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _root, references = _configured_project(tmp_path / "research")
        reference = {**references[0], "start_line": start, "end_line": end}
        reference.pop("event_id")
        with pytest.raises(ValueError, match="beyond the source file"):
            server.memory_fetch_evidence(**reference, context_lines=0)
    finally:
        server.configure_runtime(*prior)


def test_shortened_source_preserves_the_original_reference_but_cannot_pass(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root, references = _configured_project(tmp_path / "research")
        (root / "docs/runtime.md").write_text("", encoding="utf-8")
        fetched = server.memory_fetch_evidence(**references[0])
        assert fetched["claim_gate_reference"] == references[0]
        assert fetched["range_valid"] is False
        assert fetched["content_withheld"] is True
        assert fetched["context_start_line"] is None
        assert server.memory_check_evidence_eligibility(
            "Material result.", "result", "material", [fetched["claim_gate_reference"]],
        )["status"] == "blocked"
    finally:
        server.configure_runtime(*prior)


def test_candidate_rows_are_compacted_and_sourceless_rows_carry_suggestions(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        _configured_project(tmp_path / "research")
        result = server.memory_search_candidates("Claim gate evidence", mode="lexical")
        for candidate in result["results"]:
            assert "candidate_only" not in candidate
            assert all(value is not None for value in candidate.values())
        approval = server.memory_search_candidates("approval_claim_gate", mode="lexical")
        row = next(
            item for item in approval["results"]
            if item["event_id"] == "approval_claim_gate"
        )
        # The approval summary shares no passage tokens, so suggestions are
        # optional here; when present they must be complete fetchable locators.
        for suggestion in row.get("suggested_registered_evidence", []):
            assert suggestion["candidate_only"] is True
            assert suggestion["path"] and suggestion["source_sha256"]
            assert suggestion["start_line"] >= 1
    finally:
        server.configure_runtime(*prior)


def test_mismatched_fetch_returns_actionable_guidance(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root, references = _configured_project(tmp_path / "research")
        (root / "docs/runtime.md").write_text(
            "Runtime contract\nChanged after indexing.\n", encoding="utf-8",
        )
        fetched = server.memory_fetch_evidence(**references[0], context_lines=0)
        assert fetched["integrity_status"] == "mismatched"
        guidance = fetched["mismatch_guidance"]
        assert guidance["current_file_matches_registered_revision"] is False
        assert guidance["events_bound_to_current_revision"] == []
        assert "do not assert" in guidance["note"]
    finally:
        server.configure_runtime(*prior)


def test_receipt_discloses_fetched_but_uncited_mismatched_evidence(tmp_path: Path) -> None:
    prior = (server.ROOT, server.RESEARCH_DB, server.EVENTS_ROOT)
    try:
        root, references = _configured_project(tmp_path / "omission")
        runtime_ref, audit_ref = references
        # The runtime source drifts after registration; the session fetches it
        # and sees the mismatch, then cites only the intact audit source.
        (root / "docs/runtime.md").write_text(
            "Runtime contract\nGPU offload is now enabled.\n", encoding="utf-8",
        )
        server._SESSION_FETCH_LOG.clear()
        fetched = server.memory_fetch_evidence(**runtime_ref, context_lines=0)
        assert fetched["integrity_status"] == "mismatched"
        receipt = server.memory_check_evidence_eligibility(
            "Joint packing is required.", "result", "material", [audit_ref],
        )
        assert receipt["session_omitted_mismatched_fetches"] == 1
        disclosure = receipt["session_fetch_disclosure"]
        listed = disclosure["fetched_not_cited_mismatched"]
        assert [entry["event_id"] for entry in listed] == ["observation_runtime"]
        assert "abstain" in disclosure["note"]
        # Citing the mismatched reference itself clears the disclosure: the
        # gate then judges it directly instead of reporting an omission.
        cited = server.memory_check_evidence_eligibility(
            "Joint packing is required.", "result", "material", [audit_ref, runtime_ref],
        )
        assert cited["session_omitted_mismatched_fetches"] == 0
        assert "session_fetch_disclosure" not in cited
        # Intact fetches never appear in the disclosure.
        server._SESSION_FETCH_LOG.clear()
        server.memory_fetch_evidence(**audit_ref, context_lines=0)
        clean = server.memory_check_evidence_eligibility(
            "Joint packing is required.", "result", "material", [audit_ref],
        )
        assert clean["session_omitted_mismatched_fetches"] == 0
    finally:
        server._SESSION_FETCH_LOG.clear()
        server.configure_runtime(*prior)
