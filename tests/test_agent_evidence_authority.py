from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from universal_research_mcp.governance.hashing import artifact_hash
from universal_research_mcp.agent_runtime import ProjectEvidenceBundleBuilder


_NOW = "2026-08-04T00:00:00+00:00"


def _source_ref(digest: str, *, verification: str = "integrity_verified") -> dict:
    return {
        "artifact_revision_id": f"artifact_evidence@sha256:{digest}",
        "locator": {
            "kind": "line_range",
            "path": "docs/evidence.md",
            "start": 1,
            "end": 1,
        },
        "verification_status": verification,
    }


def _core_record(
    record_id: str,
    *,
    kind: str = "observation",
    status: str = "completed",
    payload: dict | None = None,
    source_refs: list[dict] | None = None,
    relations: list[dict] | None = None,
) -> dict:
    record = {
        "schema_version": "core/1.0",
        "record_id": record_id,
        "record_kind": kind,
        "occurred_at": _NOW,
        "recorded_at": _NOW,
        "status": status,
        "created_by": {"actor_id": "actor_fixture", "actor_type": "human"},
        "payload": payload if payload is not None else {"summary": "fixture"},
    }
    if source_refs is not None:
        record["source_refs"] = source_refs
    if relations is not None:
        record["relations"] = relations
    return record


def _amendment(
    target: str,
    *,
    status: str = "completed",
    corrected_value: str = "corrected",
) -> dict:
    return _core_record(
        "amendment_fixture",
        kind="amendment",
        status=status,
        relations=[{"type": "corrects", "target_id": target}],
        payload={
            "path": "/payload/summary",
            "recorded_value": "recorded",
            "corrected_value": corrected_value,
            "reason": "fixture correction",
        },
    )


def _write_project(
    root: Path,
    records: list[dict],
    *,
    content: str = "evidence\n",
    registration_extra: dict | None = None,
) -> str:
    source = root / "docs/evidence.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    events = root / "data/events"
    daily = events / "daily/2026-08-04"
    daily.mkdir(parents=True, exist_ok=True)
    registration = {
        "source_id": "source_fixture",
        "source_path": "docs/evidence.md",
        "source_sha256": digest,
    }
    registration.update(registration_extra or {})
    (events / "sources.jsonl").write_text(
        json.dumps(registration) + "\n", encoding="utf-8"
    )
    (daily / "events.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return digest


def _packet(record_id: str) -> dict:
    return {
        "agent_id": "retrieval_governor",
        "scope": {
            "allowed_paths": ["docs/**"],
            "allowed_sources": ["canonical"],
        },
        "evidence_boundary": {
            "record_ids": [record_id],
            "result_ids": [],
            "dataset_hashes": [],
            "model_hashes": [],
            "artifact_revisions": [],
            "commit_ids": [],
            "snapshot_hash": "sha256:" + "0" * 64,
        },
    }


def _seal(builder: ProjectEvidenceBundleBuilder, packet: dict, root: Path):
    preview = builder.preview(packet, root)
    packet["evidence_boundary"]["snapshot_hash"] = preview.snapshot_hash
    return builder.build(packet, root)


def test_core_record_cannot_bypass_source_refs_with_legacy_source(
    tmp_path: Path,
) -> None:
    digest = _write_project(tmp_path, [])
    record = _core_record("observation_bypass")
    record["source"] = {
        "source_path": "docs/evidence.md",
        "source_sha256": digest,
        "line_start": 1,
        "line_end": 1,
    }
    daily = tmp_path / "data/events/daily/2026-08-04/events.jsonl"
    daily.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="canonical event validation failed"):
        ProjectEvidenceBundleBuilder().preview(_packet("observation_bypass"), tmp_path)


def test_unverified_core_reference_is_not_prompt_evidence(tmp_path: Path) -> None:
    digest = _write_project(tmp_path, [])
    record = _core_record(
        "observation_unverified",
        source_refs=[_source_ref(digest, verification="unverified")],
    )
    ledger = tmp_path / "data/events/daily/2026-08-04/events.jsonl"
    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not integrity verified"):
        ProjectEvidenceBundleBuilder().preview(
            _packet("observation_unverified"), tmp_path
        )


def test_completed_amendment_is_applied_and_bound_to_snapshot(tmp_path: Path) -> None:
    digest = _write_project(tmp_path, [])
    original = _core_record(
        "observation_original",
        payload={"summary": "recorded"},
        source_refs=[_source_ref(digest)],
    )
    ledger = tmp_path / "data/events/daily/2026-08-04/events.jsonl"
    ledger.write_text(
        json.dumps(original)
        + "\n"
        + json.dumps(_amendment("observation_original"))
        + "\n",
        encoding="utf-8",
    )

    bundle = _seal(
        ProjectEvidenceBundleBuilder(), _packet("observation_original"), tmp_path
    )

    authority = bundle.authority_records[0]
    assert authority["canonical_record_hash"] == artifact_hash(original)
    assert authority["resolved_record_hash"] != authority["canonical_record_hash"]
    assert authority["current_view"]["is_amended"] is True
    assert authority["current_view"]["applied_amendments"][0]["amendment_id"] == (
        "amendment_fixture"
    )
    assert authority["source"]["verification_status"] == "integrity_verified"
    assert bundle.passages[0].content == "evidence"


def test_draft_amendment_blocks_but_rejected_amendment_does_not_apply(
    tmp_path: Path,
) -> None:
    digest = _write_project(tmp_path, [])
    original = _core_record(
        "observation_original",
        payload={"summary": "recorded"},
        source_refs=[_source_ref(digest)],
    )
    ledger = tmp_path / "data/events/daily/2026-08-04/events.jsonl"
    draft = _amendment("observation_original", status="draft")
    ledger.write_text(
        json.dumps(original) + "\n" + json.dumps(draft) + "\n",
        encoding="utf-8",
    )
    builder = ProjectEvidenceBundleBuilder()
    with pytest.raises(ValueError, match="pending amendment"):
        builder.preview(_packet("observation_original"), tmp_path)

    rejected = {**draft, "status": "rejected"}
    ledger.write_text(
        json.dumps(original) + "\n" + json.dumps(rejected) + "\n",
        encoding="utf-8",
    )
    bundle = _seal(builder, _packet("observation_original"), tmp_path)
    assert bundle.authority_records[0]["current_view"]["is_amended"] is False

    malformed = deepcopy(rejected)
    malformed["relations"] = [{"type": "corrects"}]
    ledger.write_text(
        json.dumps(original) + "\n" + json.dumps(malformed) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="canonical event validation failed"):
        builder.preview(_packet("observation_original"), tmp_path)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (
            _core_record(
                "observation_superseded",
                status="superseded",
                source_refs=[],
            ),
            "not claim-eligible",
        ),
        (
            _core_record(
                "claim_withdrawn",
                kind="claim",
                payload={"support_status": "withdrawn"},
                source_refs=[],
            ),
            "withdrawn or retracted",
        ),
    ],
)
def test_superseded_or_withdrawn_core_record_is_rejected(
    tmp_path: Path, record: dict, message: str
) -> None:
    digest = _write_project(tmp_path, [])
    record["source_refs"] = [_source_ref(digest)]
    ledger = tmp_path / "data/events/daily/2026-08-04/events.jsonl"
    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        ProjectEvidenceBundleBuilder().preview(_packet(record["record_id"]), tmp_path)


def test_completed_superseding_relation_invalidates_old_record(tmp_path: Path) -> None:
    digest = _write_project(tmp_path, [])
    original = _core_record("observation_old", source_refs=[_source_ref(digest)])
    replacement = _core_record(
        "observation_new",
        relations=[{"type": "supersedes", "target_id": "observation_old"}],
    )
    ledger = tmp_path / "data/events/daily/2026-08-04/events.jsonl"
    ledger.write_text(
        json.dumps(original) + "\n" + json.dumps(replacement) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="was superseded"):
        ProjectEvidenceBundleBuilder().preview(_packet("observation_old"), tmp_path)


@pytest.mark.parametrize("component", ["leaf", "directory"])
def test_source_path_rejects_every_symlink_component(
    tmp_path: Path, component: str
) -> None:
    real = tmp_path / "real/evidence.md"
    real.parent.mkdir(parents=True)
    real.write_text("evidence\n", encoding="utf-8")
    digest = hashlib.sha256(real.read_bytes()).hexdigest()
    docs = tmp_path / "docs"
    if component == "leaf":
        docs.mkdir()
        (docs / "evidence.md").symlink_to(real)
        source_path = "docs/evidence.md"
    else:
        docs.mkdir()
        (docs / "linked").symlink_to(real.parent, target_is_directory=True)
        source_path = "docs/linked/evidence.md"
    events = tmp_path / "data/events"
    daily = events / "daily/2026-08-04"
    daily.mkdir(parents=True)
    (events / "sources.jsonl").write_text(
        json.dumps(
            {
                "source_id": "source_fixture",
                "source_path": source_path,
                "source_sha256": digest,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_ref = _source_ref(digest)
    source_ref["locator"]["path"] = source_path
    record = _core_record("observation_symlink", source_refs=[source_ref])
    (daily / "events.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    packet = _packet("observation_symlink")
    packet["scope"]["allowed_paths"] = ["docs/**"]

    with pytest.raises(ValueError, match="symlink or is inaccessible"):
        ProjectEvidenceBundleBuilder().preview(packet, tmp_path)


def test_project_root_symlink_alias_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real_project"
    digest = _write_project(real_root, [])
    record = _core_record("observation_root_alias", source_refs=[_source_ref(digest)])
    ledger = real_root / "data/events/daily/2026-08-04/events.jsonl"
    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
    alias = tmp_path / "project_alias"
    alias.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="root path contains a symlink"):
        ProjectEvidenceBundleBuilder().preview(_packet("observation_root_alias"), alias)


def test_authority_metadata_only_change_alters_snapshot(tmp_path: Path) -> None:
    digest = _write_project(
        tmp_path, [], registration_extra={"source_type": "markdown"}
    )
    record = _core_record("observation_metadata", source_refs=[_source_ref(digest)])
    ledger = tmp_path / "data/events/daily/2026-08-04/events.jsonl"
    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
    builder = ProjectEvidenceBundleBuilder()
    sealed_packet = _packet("observation_metadata")
    first = builder.preview(sealed_packet, tmp_path)
    sealed_packet["evidence_boundary"]["snapshot_hash"] = first.snapshot_hash

    manifest = tmp_path / "data/events/sources.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "source_id": "source_fixture",
                "source_path": "docs/evidence.md",
                "source_sha256": digest,
                "source_type": "plain_text",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    second = builder.preview(_packet("observation_metadata"), tmp_path)

    assert first.passages == second.passages
    assert first.snapshot_hash != second.snapshot_hash
    assert (
        first.authority_records[0]["source"]["registration_hash"]
        != second.authority_records[0]["source"]["registration_hash"]
    )
    with pytest.raises(ValueError, match="declared evidence snapshot"):
        builder.build(sealed_packet, tmp_path)


def test_candidate_file_count_is_capped_before_any_ledger_load(tmp_path: Path) -> None:
    events = tmp_path / "data/events"
    events.mkdir(parents=True)
    (events / "sources.jsonl").write_text("", encoding="utf-8")
    for day in ("2026-08-01", "2026-08-02", "2026-08-03"):
        ledger = events / f"daily/{day}/events.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="ledger file limit"):
        ProjectEvidenceBundleBuilder(max_ledger_files=2).preview(
            _packet("observation_missing"), tmp_path
        )


def test_only_completed_exact_hash_legacy_event_is_eligible(tmp_path: Path) -> None:
    digest = _write_project(tmp_path, [])
    legacy = {
        "schema_version": "1.0",
        "event_id": "event_legacy",
        "date": "2026-08-04",
        "event_type": "observation",
        "status": "completed",
        "project": "fixture",
        "summary": "legacy exact source",
        "source": {
            "source_path": "docs/evidence.md",
            "source_sha256": digest,
            "line_start": 1,
            "line_end": 1,
        },
    }
    ledger = tmp_path / "data/events/daily/2026-08-04/events.jsonl"
    ledger.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    bundle = _seal(ProjectEvidenceBundleBuilder(), _packet("event_legacy"), tmp_path)
    assert bundle.authority_records[0]["record_family"] == "legacy"
    assert bundle.authority_records[0]["source"]["verification_status"] == (
        "legacy_exact_hash"
    )

    legacy["status"] = "active"
    ledger.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="completed status"):
        ProjectEvidenceBundleBuilder().preview(_packet("event_legacy"), tmp_path)
