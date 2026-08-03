from __future__ import annotations

from pathlib import Path

import pytest

from universal_research_mcp.agent_runtime.store import RuntimeStoreError, SessionStore


def _create_run(store: SessionStore, run_id: str = "run_fixture") -> None:
    store.create_run(
        run_id,
        {
            "schema_version": "agent-runtime-run-manifest/1.0",
            "run_id": run_id,
        },
        {
            "schema_version": "agent-run-plan/1.0",
            "run_id": run_id,
            "run_plan_hash": "sha256:" + "a" * 64,
        },
    )


def test_store_never_creates_through_an_intermediate_symlink(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "data").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeStoreError, match="symlink|non-directory"):
        _create_run(store)

    assert not (outside / "governance").exists()


def test_store_rejects_session_directory_swapped_for_symlink(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    _create_run(store)
    sessions = store.run_dir("run_fixture") / "sessions"
    sessions.rmdir()
    outside = tmp_path.parent / f"{tmp_path.name}-sessions-outside"
    outside.mkdir()
    sessions.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeStoreError, match="symlink|non-directory"):
        store.create_session(
            "run_fixture",
            "session_fixture",
            agent_id="retrieval_governor",
            manifest={"session_id": "session_fixture", "agent_id": "retrieval_governor"},
            task={"agent_id": "retrieval_governor"},
            evidence={"bundle_hash": "sha256:" + "b" * 64},
            prompt_template={"prompt_pack_hash": "sha256:" + "c" * 64},
        )

    assert list(outside.iterdir()) == []


def test_inspection_exposes_only_controller_derived_decision_metrics(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    _create_run(store)
    store.create_session(
        "run_fixture",
        "session_fixture",
        agent_id="retrieval_governor",
        manifest={"session_id": "session_fixture", "agent_id": "retrieval_governor"},
        task={"agent_id": "retrieval_governor"},
        evidence={"bundle_hash": "sha256:" + "b" * 64},
        prompt_template={"prompt_pack_hash": "sha256:" + "c" * 64},
    )
    store.create_session_artifact(
        "run_fixture",
        "session_fixture",
        "decision.json",
        {
            "decision": {
                "status": "pass",
                "summary": "model-authored secret prose",
                "findings": [{"code": "F-1"}],
                "evidence_refs": [{"record_id": "record_one"}],
            },
        },
    )

    report = store.inspect("run_fixture")
    rendered = str(report)
    decision = report["sessions"][0]["decision"]

    assert "model-authored secret prose" not in rendered
    assert decision["status"] == "pass"
    assert decision["finding_count"] == 1
    assert decision["evidence_reference_count"] == 1
    assert str(decision["decision_hash"]).startswith("sha256:")


def test_store_rejects_oversized_immutable_artifact(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    _create_run(store)
    store.create_session(
        "run_fixture",
        "session_fixture",
        agent_id="retrieval_governor",
        manifest={"session_id": "session_fixture", "agent_id": "retrieval_governor"},
        task={"agent_id": "retrieval_governor"},
        evidence={"bundle_hash": "sha256:" + "b" * 64},
        prompt_template={"prompt_pack_hash": "sha256:" + "c" * 64},
    )

    with pytest.raises(RuntimeStoreError, match="size limit"):
        store.create_session_artifact(
            "run_fixture",
            "session_fixture",
            "raw-output.json",
            {"raw": "x" * (8 * 1024 * 1024)},
        )


def test_store_rejects_ledger_swapped_for_symlink(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    _create_run(store)
    ledger = store.run_dir("run_fixture") / "events.jsonl"
    ledger.unlink()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-ledger"
    outside.write_text("outside\n", encoding="utf-8")
    ledger.symlink_to(outside)

    with pytest.raises(RuntimeStoreError, match="symlink"):
        store.read_events("run_fixture")

    assert outside.read_text(encoding="utf-8") == "outside\n"
