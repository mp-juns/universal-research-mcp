from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest
from jsonschema import Draft202012Validator

from universal_research_mcp.governance.hashing import artifact_hash
from universal_research_mcp.secure_harness.approval import (
    HarnessApprovalError,
    HarnessApprovalStore,
)
from universal_research_mcp.secure_harness.claims import evaluate_segments
from universal_research_mcp.secure_harness.codex_runner import (
    CodexRunner, codex_command, codex_environment,
)
from universal_research_mcp.secure_harness.contracts import HarnessContractError
from universal_research_mcp.secure_harness.controller import (
    HarnessRunStore,
    apply_changes,
    build_plan_bundle,
    change_review,
    preflight,
)
from universal_research_mcp.secure_harness.docker_backend import DockerBackend, docker_command
from universal_research_mcp.secure_harness.snapshot import materialize_snapshot
from universal_research_mcp.secure_harness.worker import WorkerSession


IMAGE = "example/research-worker@sha256:" + "a" * 64


def _spec(*, operations: list[dict] | None = None, approval_mode: str = "plan_once") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "run_id": "run_secure_01",
        "workflow_id": "workflow_secure_01",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "verification_mode": "adaptive",
        "approval_mode": approval_mode,
        "image": IMAGE,
        "resources": {
            "cpus": 2,
            "memory_mb": 1024,
            "pids": 64,
            "max_parallelism": 1,
            "max_total_tokens": 100_000,
            "max_cost_usd": 0,
        },
        "operations": operations or [{
            "schema_version": "worker-operation/1.0",
            "operation_id": "test_01",
            "kind": "test",
            "paths": ["src/example.py"],
            "argv": ["python", "-m", "pytest", "-q"],
            "cwd": ".",
            "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
            "timeout_seconds": 60,
            "network": False,
            "gpu_devices": [],
        }],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src/example.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def _fake_doctor(command, **_kwargs):
    return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")


def test_plan_is_hash_bound_and_rejects_raw_shell_network_and_protected_paths(tmp_path: Path) -> None:
    root = _project(tmp_path)
    bundle = build_plan_bundle(root, _spec())
    assert bundle["plan"]["snapshot_hash"] == bundle["snapshot_manifest"]["snapshot_hash"]
    assert bundle["plan"]["run_plan_hash"].startswith("sha256:")

    tampered = json.loads(json.dumps(bundle["plan"]))
    tampered["resources"]["cpus"] = 3
    with pytest.raises(HarnessContractError, match="hash mismatch"):
        from universal_research_mcp.secure_harness.contracts import validate_run_plan
        validate_run_plan(tampered)

    for mutation, message in (
        ({"command": "curl evil.invalid"}, "unsupported fields"),
        ({"network": True}, "cannot request network"),
        ({"paths": [".git/config"]}, "allowed project surface"),
    ):
        spec = _spec()
        spec["operations"][0].update(mutation)
        with pytest.raises(HarnessContractError, match=message):
            build_plan_bundle(root, spec)
    for argv in (["sh", "-c", "curl evil.invalid"], ["python", "-c", "print(1)"]):
        spec = _spec()
        spec["operations"][0]["argv"] = argv
        with pytest.raises(HarnessContractError, match="cannot"):
            build_plan_bundle(root, spec)


def test_runtime_plan_matches_published_json_schema(tmp_path: Path) -> None:
    root = _project(tmp_path)
    plan = build_plan_bundle(root, _spec())["plan"]
    schema = json.loads((Path(__file__).parents[1] / "schemas/research-run-plan.schema.json").read_text())
    Draft202012Validator(schema).validate(plan)


def test_gpu_requires_experiment_and_exact_uuid(tmp_path: Path) -> None:
    root = _project(tmp_path)
    spec = _spec()
    spec["operations"][0]["gpu_devices"] = ["0"]
    with pytest.raises(HarnessContractError, match="exact GPU UUID"):
        build_plan_bundle(root, spec)
    spec = _spec()
    spec["operations"][0]["gpu_devices"] = ["GPU-ced89e32-8c4b-38bc-4b71-8be9d46c5f9f"]
    with pytest.raises(HarnessContractError, match="restricted to experiment"):
        build_plan_bundle(root, spec)


def test_one_time_approval_binds_project_plan_snapshot_and_resources(tmp_path: Path) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    bundle = build_plan_bundle(root, _spec())
    store = HarnessApprovalStore(root, state_root=state)
    summary = store.create(
        bundle["plan"],
        expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    assert summary["approval_hash"].startswith("sha256:")
    consumed = store.consume(bundle["plan"])
    assert consumed["run_plan_hash"] == bundle["plan"]["run_plan_hash"]
    with pytest.raises(HarnessApprovalError, match="already consumed"):
        store.consume(bundle["plan"])


def test_preflight_detects_snapshot_drift_before_execution(tmp_path: Path) -> None:
    root = _project(tmp_path)
    bundle = build_plan_bundle(root, _spec())
    assert preflight(root, bundle, docker_runner=_fake_doctor)["valid"] is True
    (root / "src/example.py").write_text("VALUE = 2\n", encoding="utf-8")
    report = preflight(root, bundle, docker_runner=_fake_doctor)
    assert report["valid"] is False
    assert {item["code"] for item in report["issues"]} == {"SNAPSHOT_DRIFT"}


def test_docker_command_is_offline_unprivileged_bounded_and_digest_pinned(tmp_path: Path) -> None:
    root = _project(tmp_path)
    bundle = build_plan_bundle(root, _spec())
    workspace = tmp_path / "workspace"
    materialize_snapshot(root, bundle["snapshot_manifest"], workspace)
    command = docker_command(bundle["plan"], "test_01", workspace)
    rendered = " ".join(command)
    assert "--network none" in rendered
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges:true" in command
    assert "/var/run/docker.sock" not in rendered
    assert IMAGE in command
    assert command[-4:] == ["python", "-m", "pytest", "-q"]


def test_worker_allows_only_matching_operation_and_never_replays_recipe(tmp_path: Path) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    bundle = build_plan_bundle(root, _spec())
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "manifest.json"
    plan_path.write_text(json.dumps(bundle["plan"]), encoding="utf-8")
    manifest_path.write_text(json.dumps(bundle["snapshot_manifest"]), encoding="utf-8")
    HarnessApprovalStore(root, state_root=state).create(
        bundle["plan"],
        expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )

    calls = []
    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="passed", stderr="")

    session = WorkerSession(
        project_root=root,
        plan_path=plan_path,
        manifest_path=manifest_path,
        workspace=tmp_path / "workspace",
        approval_store=HarnessApprovalStore(root, state_root=state),
        backend=DockerBackend(run),
    )
    with pytest.raises(HarnessContractError, match="does not match"):
        session.read("test_01", "src/example.py", 1, 1)
    assert session.execute("test_01")["success"] is True
    with pytest.raises(HarnessContractError, match="already been executed"):
        session.execute("test_01")
    assert len(calls) == 1


def test_codex_command_exposes_only_worker_mcp_and_disables_general_tools(tmp_path: Path) -> None:
    root = _project(tmp_path)
    plan = build_plan_bundle(root, _spec())["plan"]
    for name in ("plan.json", "manifest.json", "schema.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    command = codex_command(
        plan,
        control_root=tmp_path,
        project_root=root,
        plan_path=tmp_path / "plan.json",
        manifest_path=tmp_path / "manifest.json",
        workspace_path=tmp_path / "workspace",
        schema_path=tmp_path / "schema.json",
    )
    rendered = " ".join(command)
    assert "features.shell_tool=false" in command
    assert "features.multi_agent=false" in command
    assert "tools.web_search=false" in command
    assert "features.image_generation=false" in command
    assert "tools.view_image=false" in command
    assert "--ignore-user-config" in command
    assert "--sandbox" in command and "read-only" in command
    assert "mcp_servers.ur_worker.required=true" in command
    assert "danger-full-access" not in rendered
    assert "OPENAI_API_KEY" not in codex_environment({"PATH": "/bin", "OPENAI_API_KEY": "secret"})


def test_codex_event_audit_rejects_forbidden_shell_even_if_configuration_regresses(tmp_path: Path) -> None:
    root = _project(tmp_path)
    plan = build_plan_bundle(root, _spec())["plan"]
    forbidden = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "whoami"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ])
    runner = CodexRunner(lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=forbidden, stderr=""))
    with pytest.raises(HarnessContractError, match="forbidden tool surface"):
        runner.run(
            plan,
            prompt="Use only the worker.",
            control_root=tmp_path,
            project_root=root,
            plan_path=tmp_path / "plan.json",
            manifest_path=tmp_path / "manifest.json",
            workspace_path=tmp_path / "workspace",
            schema_path=tmp_path / "schema.json",
        )


def test_claim_renderer_blocks_material_claims_without_trusted_receipts() -> None:
    segments = [{
        "claim_id": "claim_metric",
        "text": "The governed condition improved citation validity by 20%.",
        "kind": "result",
        "final": True,
        "external": False,
        "numerical": True,
        "citation": True,
        "benchmark": True,
        "causal": False,
        "canonical": False,
        "conflicting": False,
        "evidence_refs": ["result_01"],
    }]
    blocked = evaluate_segments(segments, verification_mode="adaptive")
    assert blocked["status"] == "blocked"
    receipt = {
        "claim_id": "claim_metric",
        "evidence_refs": ["result_01"],
        "retrieval_passed": True,
        "source_verification_passed": True,
        "independent_review_passed": True,
    }
    receipt["receipt_hash"] = artifact_hash(receipt)
    passed = evaluate_segments(segments, verification_mode="adaptive", verification_receipts=[receipt])
    assert passed["status"] == "passed"
    assert passed["answer"].startswith("The governed condition")


def test_change_import_requires_unchanged_base_and_exact_diff_hash(tmp_path: Path) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    operations = [{
        "schema_version": "worker-operation/1.0",
        "operation_id": "patch_01",
        "kind": "patch",
        "paths": ["src/example.py"],
        "argv": [],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 60,
        "network": False,
        "gpu_devices": [],
    }]
    bundle = build_plan_bundle(root, _spec(operations=operations))
    store = HarnessRunStore(root, state_root=state)
    run = store.create(bundle, "Edit one file.")
    materialize_snapshot(root, bundle["snapshot_manifest"], run / "workspace")
    (run / "workspace/src/example.py").write_text("VALUE = 2\n", encoding="utf-8")
    review = change_review(root, "run_secure_01", state_root=state)
    assert review["changes"][0]["path"] == "src/example.py"
    with pytest.raises(HarnessContractError, match="does not match"):
        apply_changes(root, "run_secure_01", confirm_diff_hash="sha256:" + "0" * 64, state_root=state)
    result = apply_changes(root, "run_secure_01", confirm_diff_hash=review["diff_hash"], state_root=state)
    assert result["applied_paths"] == ["src/example.py"]
    assert (root / "src/example.py").read_text(encoding="utf-8") == "VALUE = 2\n"
