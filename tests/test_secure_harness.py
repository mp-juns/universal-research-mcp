from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import select
import subprocess
import sys
from types import SimpleNamespace

from mcp.types import LATEST_PROTOCOL_VERSION
import pytest
from jsonschema import Draft202012Validator

from universal_research_mcp.governance.hashing import artifact_hash, hash_without
from universal_research_mcp.core.input import append_record, validate_candidate_records
from universal_research_mcp.indexing import initialize_project
from universal_research_mcp.secure_harness.approval import (
    HarnessApprovalError,
    HarnessApprovalStore,
)
from universal_research_mcp.secure_harness.claims import evaluate_segments
from universal_research_mcp.secure_harness.codex_runner import (
    CodexRunner, CodexTokenCeilingError, CodexWorkerProcessError,
    build_worker_tool_receipt, codex_command, codex_environment,
    validate_worker_tool_receipts, worker_mcp_environment,
)
from universal_research_mcp.secure_harness.contracts import HarnessContractError
from universal_research_mcp.secure_harness.controller import (
    HarnessRunStore,
    apply_changes,
    attest_run,
    build_plan_bundle,
    change_review,
    execute_codex,
    preflight,
    review_run,
    validate_codex_result_record,
)
from universal_research_mcp.secure_harness.docker_backend import DockerBackend, docker_command
from universal_research_mcp.secure_harness.snapshot import materialize_snapshot
from universal_research_mcp.secure_harness.test_contracts import verify_test_contracts
from universal_research_mcp.secure_harness.worker import WorkerSession


IMAGE = "example/research-worker@sha256:" + "a" * 64


def _spec(
    *,
    operations: list[dict] | None = None,
    approval_mode: str = "plan_once",
    workflow_mode: str = "lightweight",
    verification_mode: str = "adaptive",
) -> dict:
    now = datetime.now(timezone.utc)
    resolved_operations = operations or [{
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
    }]
    test_contracts = [
        {
            "schema_version": "harness-test-contract/1.0",
            "contract_id": f"contract_{operation['operation_id']}",
            "operation_id": operation["operation_id"],
            "checks": [{
                "check_id": "source_value_symbol",
                "path": "src/example.py",
                "kind": "python_symbol",
                "selector": "VALUE",
                "expected": True,
            }],
        }
        for operation in resolved_operations
        if operation["kind"] == "test"
    ]
    return {
        "run_id": "run_secure_01",
        "workflow_id": "workflow_secure_01",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "workflow_mode": workflow_mode,
        "verification_mode": verification_mode,
        "approval_mode": approval_mode,
        "agent_creation_disclosure": {
            "schema_version": "agent-creation-disclosure/1.0",
            "reason": "Use one isolated Codex worker for the sealed research operation.",
            "delegated_tasks": ["Execute the exact sealed worker plan."],
            "agent_count": 1,
            "direct_execution_alternative": "The host could execute the sealed operations sequentially without an agent.",
            "expected_additional_tokens": {
                "minimum": 0, "likely": 10_000, "maximum": 100_000,
            },
            "expected_elapsed_minutes": {
                "minimum": 1, "likely": 5, "maximum": 60,
            },
            "scope": {
                "paths": sorted({path for item in resolved_operations for path in item["paths"]}),
                "network": False,
                "model_execution": True,
                "writes": any(
                    item["kind"] in {"patch", "test", "build", "experiment"}
                    for item in resolved_operations
                ),
            },
        },
        "image": IMAGE,
        "resources": {
            "cpus": 2,
            "memory_mb": 1024,
            "pids": 64,
            "max_parallelism": 1,
            "max_total_tokens": 100_000,
            "max_cost_usd": 0,
        },
        "operations": resolved_operations,
        "test_contracts": test_contracts,
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


def _execute_receipt(plan: dict, *, stdout: str = "passed", stderr: str = "") -> dict:
    operation = plan["operations"][0]
    return build_worker_tool_receipt({
        "id": "call_execute_01",
        "type": "mcp_tool_call",
        "server": "ur_worker",
        "tool": "worker_execute",
        "status": "completed",
        "arguments": {"operation_id": operation["operation_id"]},
        "result": {
            "content": [],
            "isError": False,
            "structuredContent": {
                "operation_id": operation["operation_id"],
                "exit_code": 0,
                "stdout": stdout,
                "stderr": stderr,
                "command_hash": artifact_hash({"argv": operation["argv"]}),
                "success": True,
            },
        },
    }, plan, sequence=1)


def _read_operation() -> dict:
    return {
        "schema_version": "worker-operation/1.0",
        "operation_id": "read_source_01",
        "kind": "read",
        "paths": ["src/example.py"],
        "argv": [],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 60,
        "network": False,
        "gpu_devices": [],
    }


def _read_item(
    root: Path, *, content: str = "VALUE = 1", server: str = "ur_worker",
    tool: str = "worker_read",
) -> dict:
    return {
        "id": "call_read_01",
        "type": "mcp_tool_call",
        "server": server,
        "tool": tool,
        "status": "completed",
        "arguments": {
            "operation_id": "read_source_01", "path": "src/example.py",
            "start_line": 1, "end_line": 1,
        },
        "result": {
            "content": [{"type": "text", "text": content}],
            "isError": False,
            "structuredContent": {
                "operation_id": "read_source_01", "path": "src/example.py",
                "start_line": 1, "end_line": 1, "content": content,
                "sha256": hashlib.sha256((root / "src/example.py").read_bytes()).hexdigest(),
            },
        },
    }


def test_plan_is_hash_bound_and_rejects_raw_shell_network_and_protected_paths(tmp_path: Path) -> None:
    root = _project(tmp_path)
    missing_disclosure = _spec()
    missing_disclosure.pop("agent_creation_disclosure")
    with pytest.raises(HarnessContractError, match="disclosure"):
        build_plan_bundle(root, missing_disclosure)
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


def test_benchmark_and_final_review_plans_require_strict_verification(tmp_path: Path) -> None:
    root = _project(tmp_path)
    with pytest.raises(HarnessContractError, match="require strict verification"):
        build_plan_bundle(root, _spec(workflow_mode="benchmark"))
    bundle = build_plan_bundle(root, _spec(workflow_mode="benchmark", verification_mode="strict"))
    assert bundle["plan"]["workflow_mode"] == "benchmark"


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


def test_agent_process_is_never_created_without_preconsumed_exact_approval(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    bundle = build_plan_bundle(root, _spec())
    calls = 0

    class Runner:
        def run(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("runner must not be called")

    with pytest.raises(HarnessApprovalError, match="missing"):
        execute_codex(
            root,
            bundle,
            prompt="Run only the sealed operation.",
            state_root=state,
            runner=Runner(),
        )
    assert calls == 0


def test_agent_approval_is_consumed_before_runner_and_replay_is_blocked(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    bundle = build_plan_bundle(root, _spec())
    approval_store = HarnessApprovalStore(root, state_root=state)
    approval_store.create(
        bundle["plan"],
        expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    calls = 0

    class Runner:
        def run(self, plan, **kwargs):
            nonlocal calls
            calls += 1
            consumed = approval_store.verify_consumed(plan)
            assert consumed["agent_creation_disclosure_hash"].startswith("sha256:")
            assert kwargs["approval_state_root"] == approval_store.state_root
            return SimpleNamespace(
                model=plan["model"],
                usage={
                    "input_tokens": 1, "cached_input_tokens": 0,
                    "output_tokens": 1, "reasoning_output_tokens": 0,
                    "total_tokens": 2,
                },
                events_hash=artifact_hash({"events": 1}),
                final_output={"segments": []},
                tool_receipts=[_execute_receipt(plan)],
            )

    result = execute_codex(
        root,
        bundle,
        prompt="Run only the sealed operation.",
        state_root=state,
        runner=Runner(),
    )
    assert calls == 1
    assert result["agent_creation_approval_consumption_hash"].startswith("sha256:")
    with pytest.raises(HarnessApprovalError, match="already consumed"):
        execute_codex(
            root,
            bundle,
            prompt="Run only the sealed operation.",
            state_root=state,
            runner=Runner(),
        )
    assert calls == 1


def test_worker_rejects_resealed_grant_for_a_different_agent_plan(tmp_path: Path) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    bundle = build_plan_bundle(root, _spec())
    store = HarnessApprovalStore(root, state_root=state)
    store.create(
        bundle["plan"],
        expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    store.consume(bundle["plan"])

    grant_path = store._path(bundle["plan"]["run_id"], "grant")
    grant = json.loads(grant_path.read_text(encoding="utf-8"))
    grant["model"] = "different-model"
    grant["approval_hash"] = hash_without(grant, "approval_hash")
    grant_path.write_text(json.dumps(grant), encoding="utf-8")

    with pytest.raises(HarnessApprovalError, match="exact plan"):
        store.verify_consumed(bundle["plan"])


def test_preflight_detects_snapshot_drift_before_execution(tmp_path: Path) -> None:
    root = _project(tmp_path)
    bundle = build_plan_bundle(root, _spec())
    assert preflight(root, bundle, docker_runner=_fake_doctor)["valid"] is True
    (root / "src/example.py").write_text("VALUE = 2\n", encoding="utf-8")
    report = preflight(root, bundle, docker_runner=_fake_doctor)
    assert report["valid"] is False
    assert {item["code"] for item in report["issues"]} == {
        "SNAPSHOT_DRIFT", "TEST_CONTRACT_INVALID",
    }


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
    approval_store = HarnessApprovalStore(root, state_root=state)
    approval_store.create(
        bundle["plan"],
        expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    approval_store.consume(bundle["plan"])

    calls = []
    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="passed", stderr="")

    session = WorkerSession(
        project_root=root,
        plan_path=plan_path,
        manifest_path=manifest_path,
        workspace=tmp_path / "workspace",
        approval_store=approval_store,
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
    assert "agents.enabled=false" in command
    assert "features.multi_agent=false" in command
    assert "features.multi_agent_v2=false" in command
    assert "tools.web_search=false" in command
    assert "features.image_generation=false" in command
    assert "features.view_image=false" in command
    assert "tools.view_image=false" not in command
    assert "--ignore-user-config" in command
    assert "--strict-config" in command
    assert "--sandbox" in command and "read-only" in command
    assert "mcp_servers.ur_worker.required=true" in command
    assert 'mcp_servers.ur_worker.default_tools_approval_mode="approve"' in command
    assert not any(item.startswith("approvals_reviewer=") for item in command)
    assert "--approve-for-me" not in command
    assert "danger-full-access" not in rendered
    worker_environment = worker_mcp_environment(root)
    assert worker_environment == {
        "PYTHONPATH": str(root.resolve()),
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name, value in worker_environment.items():
        assert f"mcp_servers.ur_worker.env.{name}={json.dumps(value)}" in command
    filtered = codex_environment({
        "PATH": "/bin",
        "PYTHONPATH": "/untrusted",
        "OPENAI_API_KEY": "secret",
    })
    assert filtered == {"PATH": "/bin"}


def test_worker_mcp_source_pin_imports_exact_source_from_foreign_cwd(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    foreign_cwd = tmp_path / "foreign-cwd"
    foreign_cwd.mkdir()
    environment = {
        **codex_environment(),
        **worker_mcp_environment(source_root),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pathlib, universal_research_mcp as package; "
            "print(pathlib.Path(package.__file__).resolve())",
        ],
        cwd=foreign_cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(
        (source_root / "universal_research_mcp/__init__.py").resolve()
    )


def test_worker_server_completes_real_offline_mcp_initialize_from_foreign_cwd(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).resolve().parents[1]
    root = _project(tmp_path)
    bundle = build_plan_bundle(root, _spec(operations=[{
        "schema_version": "worker-operation/1.0",
        "operation_id": "read_source_01",
        "kind": "read",
        "paths": ["src/example.py"],
        "argv": [],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 60,
        "network": False,
        "gpu_devices": [],
    }]))
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "manifest.json"
    plan_path.write_text(json.dumps(bundle["plan"]), encoding="utf-8")
    manifest_path.write_text(json.dumps(bundle["snapshot_manifest"]), encoding="utf-8")
    state = tmp_path / "state"
    approval_store = HarnessApprovalStore(root, state_root=state)
    approval_store.create(
        bundle["plan"],
        expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    approval_store.consume(bundle["plan"])
    foreign_cwd = tmp_path / "foreign-cwd"
    foreign_cwd.mkdir()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m", "universal_research_mcp.secure_harness.worker_server",
            "--root", str(root),
            "--plan", str(plan_path),
            "--manifest", str(manifest_path),
            "--workspace", str(tmp_path / "workspace"),
            "--approval-state-root", str(state),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**codex_environment(), **worker_mcp_environment(source_root)},
        cwd=foreign_cwd,
    )

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    def request(message: dict) -> dict:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        readable, _, _ = select.select([process.stdout], [], [], 10)
        assert readable, "worker server did not answer MCP within ten seconds"
        line = process.stdout.readline()
        assert line, "worker server closed stdout before its MCP response"
        return json.loads(line)

    try:
        initialized = request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "urmcp-r5-test", "version": "1.0"},
            },
        })
        assert initialized["id"] == 1
        assert initialized["result"]["serverInfo"]["name"] == (
            "Universal Research Secure Worker"
        )
        process.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }) + "\n")
        process.stdin.flush()
        tools = request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        assert tools["id"] == 2
        assert {item["name"] for item in tools["result"]["tools"]} == {
            "worker_read", "worker_search", "worker_write", "worker_execute",
            "worker_inventory",
        }
        read = request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "worker_read",
                "arguments": {
                    "operation_id": "read_source_01",
                    "path": "src/example.py",
                    "start_line": 1,
                    "end_line": 1,
                },
            },
        })
        assert read["id"] == 3
        assert read["result"]["isError"] is False
        assert read["result"]["structuredContent"] == {
            "operation_id": "read_source_01",
            "path": "src/example.py",
            "start_line": 1,
            "end_line": 1,
            "content": "VALUE = 1",
            "sha256": hashlib.sha256(
                (root / "src/example.py").read_bytes()
            ).hexdigest(),
        }
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    assert process.returncode == 0, process.stderr.read()


def test_codex_worker_nonzero_preserves_only_bounded_redacted_diagnostics(tmp_path: Path) -> None:
    root = _project(tmp_path)
    plan = build_plan_bundle(root, _spec())["plan"]
    stdout = "private model output must never appear in the exception"
    secret = "sk-proj-super-secret-value"
    stderr = "한" * 2000 + f"\nAuthorization: Bearer {secret}\ntoken={secret}\nstartup failed"
    runner = CodexRunner(
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 17, stdout=stdout, stderr=stderr,
        )
    )

    with pytest.raises(CodexWorkerProcessError) as caught:
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

    diagnostic = caught.value.diagnostic
    rendered = str(caught.value)
    assert diagnostic["returncode"] == 17
    assert diagnostic["stdout_bytes"] == len(stdout.encode("utf-8"))
    assert diagnostic["stdout_sha256"] == "sha256:" + hashlib.sha256(stdout.encode()).hexdigest()
    assert diagnostic["stderr_sha256"] == "sha256:" + hashlib.sha256(stderr.encode()).hexdigest()
    assert diagnostic["stderr_truncated"] is True
    assert len(diagnostic["stderr_tail"].encode("utf-8")) <= 4096
    assert "startup failed" in diagnostic["stderr_tail"]
    assert "[REDACTED]" in diagnostic["stderr_tail"]
    assert secret not in rendered
    assert stdout not in rendered


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


def test_codex_completed_worker_read_emits_only_bounded_plan_bound_receipt(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    plan = build_plan_bundle(root, _spec(operations=[_read_operation()]))["plan"]
    private_content = "private-read-payload-must-not-persist"
    item = _read_item(root, content=private_content)
    item["result"]["structured_content"] = item["result"].pop("structuredContent")
    item["result"].pop("isError")
    events = [
        {"type": "item.started", "item": {
            **{key: value for key, value in item.items() if key != "result"},
            "status": "inProgress",
        }},
        {"type": "item.completed", "item": item},
        {"type": "item.completed", "item": {
            "id": "message_01", "type": "agent_message",
            "text": json.dumps({"segments": []}),
        }},
        {"type": "turn.completed", "usage": {
            "input_tokens": 10, "cached_input_tokens": 2,
            "output_tokens": 3, "reasoning_output_tokens": 1,
        }},
    ]
    runner = CodexRunner(lambda *_args, **_kwargs: subprocess.CompletedProcess(
        [], 0, stdout="\n".join(json.dumps(event) for event in events), stderr="",
    ))

    result = runner.run(
        plan,
        prompt="Use only the sealed read.",
        control_root=tmp_path,
        project_root=root,
        plan_path=tmp_path / "plan.json",
        manifest_path=tmp_path / "manifest.json",
        workspace_path=tmp_path / "workspace",
        schema_path=tmp_path / "schema.json",
    )

    receipts = validate_worker_tool_receipts(result.tool_receipts, plan)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["tool"] == "worker_read"
    assert receipt["operation_id"] == "read_source_01"
    assert receipt["path"] == "src/example.py"
    assert receipt["requested_start_line"] == receipt["returned_start_line"] == 1
    assert receipt["requested_end_line"] == receipt["returned_end_line"] == 1
    assert receipt["source_sha256"] == (
        "sha256:" + hashlib.sha256((root / "src/example.py").read_bytes()).hexdigest()
    )
    assert receipt["content_sha256"] == (
        "sha256:" + hashlib.sha256(private_content.encode()).hexdigest()
    )
    serialized = json.dumps(receipts, sort_keys=True)
    assert private_content not in serialized
    assert "structuredContent" not in serialized
    assert "structured_content" not in serialized
    assert '"arguments":' not in serialized


def test_worker_receipt_accepts_exactly_one_structured_result_carrier(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    plan = build_plan_bundle(root, _spec(operations=[_read_operation()]))["plan"]
    private_content = "private-shape-fixture-must-not-persist"
    snake = _read_item(root, content=private_content)
    snake["result"]["structured_content"] = snake["result"].pop("structuredContent")
    snake["result"].pop("isError")

    receipt = build_worker_tool_receipt(snake, plan, sequence=1)
    assert receipt["result_hash"] == artifact_hash(snake["result"])
    serialized = json.dumps(receipt, sort_keys=True)
    assert private_content not in serialized
    assert "structured_content" not in serialized
    assert "structuredContent" not in serialized

    ambiguous = _read_item(root)
    ambiguous["result"]["structured_content"] = ambiguous["result"]["structuredContent"]
    with pytest.raises(HarnessContractError, match="unsupported fields") as both:
        build_worker_tool_receipt(ambiguous, plan, sequence=1)
    assert private_content not in str(both.value)

    missing = _read_item(root)
    missing["result"].pop("structuredContent")
    with pytest.raises(HarnessContractError, match="unsupported fields"):
        build_worker_tool_receipt(missing, plan, sequence=1)

    unexpected = _read_item(root)
    unexpected["result"]["private-top-level-name"] = "private-value"
    with pytest.raises(HarnessContractError, match="unsupported fields") as extra:
        build_worker_tool_receipt(unexpected, plan, sequence=1)
    diagnostic = str(extra.value)
    assert "private-top-level-name" not in diagnostic
    assert "private-value" not in diagnostic
    assert '"unexpected_key_count":1' in diagnostic


@pytest.mark.parametrize("mutation, message", [
    ({"server": "unknown-server"}, "unknown MCP server"),
    ({"tool": "unknown-tool"}, "unknown worker tool"),
    ({"status": "failed"}, "did not complete successfully"),
    ({"error": {"message": "failed"}}, "did not complete successfully"),
])
def test_codex_worker_receipt_fails_closed_on_unknown_or_failed_call(
    tmp_path: Path, mutation: dict, message: str,
) -> None:
    root = _project(tmp_path)
    plan = build_plan_bundle(root, _spec(operations=[_read_operation()]))["plan"]
    item = _read_item(root)
    item.update(mutation)
    events = [
        {"type": "item.completed", "item": item},
        {"type": "item.completed", "item": {
            "id": "message_01", "type": "agent_message",
            "text": json.dumps({"segments": []}),
        }},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]
    runner = CodexRunner(lambda *_args, **_kwargs: subprocess.CompletedProcess(
        [], 0, stdout="\n".join(json.dumps(event) for event in events), stderr="",
    ))
    with pytest.raises(HarnessContractError, match=message):
        runner.run(
            plan,
            prompt="Use only the sealed read.",
            control_root=tmp_path,
            project_root=root,
            plan_path=tmp_path / "plan.json",
            manifest_path=tmp_path / "manifest.json",
            workspace_path=tmp_path / "workspace",
            schema_path=tmp_path / "schema.json",
        )


def test_codex_success_without_machine_worker_receipt_is_ineligible(tmp_path: Path) -> None:
    root = _project(tmp_path)
    plan = build_plan_bundle(root, _spec(operations=[_read_operation()]))["plan"]
    model_only = "\n".join([
        json.dumps({"type": "item.completed", "item": {
            "id": "message_01", "type": "agent_message",
            "text": json.dumps({"segments": []}),
        }}),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 1, "output_tokens": 1,
        }}),
    ])
    runner = CodexRunner(lambda *_args, **_kwargs: subprocess.CompletedProcess(
        [], 0, stdout=model_only, stderr="",
    ))
    with pytest.raises(HarnessContractError, match="omitted worker tool receipts"):
        runner.run(
            plan,
            prompt="Use only the sealed read.",
            control_root=tmp_path,
            project_root=root,
            plan_path=tmp_path / "plan.json",
            manifest_path=tmp_path / "manifest.json",
            workspace_path=tmp_path / "workspace",
            schema_path=tmp_path / "schema.json",
        )


def test_controller_persists_and_reloads_receipt_without_raw_worker_payload(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    bundle = build_plan_bundle(root, _spec())
    approval_store = HarnessApprovalStore(root, state_root=state)
    approval_store.create(
        bundle["plan"], expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    private_stdout = "private-worker-stdout-must-not-persist"

    class Runner:
        def run(self, plan, **_kwargs):
            approval_store.verify_consumed(plan)
            return SimpleNamespace(
                model=plan["model"],
                usage={
                    "input_tokens": 1, "cached_input_tokens": 0,
                    "output_tokens": 1, "reasoning_output_tokens": 0,
                    "total_tokens": 2,
                },
                events_hash=artifact_hash({"events": "fixture"}),
                final_output={"segments": []},
                tool_receipts=[_execute_receipt(plan, stdout=private_stdout)],
            )

    summary = execute_codex(
        root, bundle, prompt="Run only the sealed operation.", state_root=state,
        runner=Runner(),
    )
    run = HarnessRunStore(root, state_root=state).run_dir(bundle["plan"]["run_id"])
    persisted = json.loads((run / "result.json").read_text(encoding="utf-8"))
    serialized = json.dumps(persisted, sort_keys=True)
    validated = validate_codex_result_record(persisted, bundle["plan"])
    assert summary["schema_version"] == "secure-harness-codex-result/2.0"
    assert summary["tool_receipts"] == validated["tool_receipts"]
    assert private_stdout not in serialized
    assert "stdout_sha256" in serialized
    persisted["tool_receipts"][0]["success"] = False
    with pytest.raises(HarnessContractError, match="integrity check failed"):
        validate_codex_result_record(persisted, bundle["plan"])


def test_bounded_receipts_cover_search_write_execute_and_inventory(tmp_path: Path) -> None:
    root = _project(tmp_path)
    search_operation = {
        **_read_operation(),
        "operation_id": "search_source_01",
        "kind": "search",
    }
    search_plan = build_plan_bundle(root, _spec(operations=[search_operation]))["plan"]
    search = build_worker_tool_receipt({
        "id": "call_search_01", "type": "mcp_tool_call", "server": "ur_worker",
        "tool": "worker_search", "status": "completed",
        "arguments": {"operation_id": "search_source_01", "query": "VALUE"},
        "result": {"content": [], "structuredContent": {
            "operation_id": "search_source_01",
            "matches": [{"path": "src/example.py", "line": 1, "text": "VALUE = 1"}],
            "truncated": False,
        }},
    }, search_plan, sequence=1)
    assert validate_worker_tool_receipts([search], search_plan)[0]["match_count"] == 1

    patch_operation = {
        **_read_operation(),
        "operation_id": "patch_source_01",
        "kind": "patch",
    }
    patch_plan = build_plan_bundle(root, _spec(operations=[patch_operation]))["plan"]
    private_replacement = "private-patch-content-must-not-persist"
    write = build_worker_tool_receipt({
        "id": "call_write_01", "type": "mcp_tool_call", "server": "ur_worker",
        "tool": "worker_write", "status": "completed",
        "arguments": {
            "operation_id": "patch_source_01", "path": "src/example.py",
            "expected_sha256": hashlib.sha256((root / "src/example.py").read_bytes()).hexdigest(),
            "content": private_replacement,
        },
        "result": {"content": [], "structuredContent": {
            "operation_id": "patch_source_01", "path": "src/example.py",
            "sha256": hashlib.sha256(private_replacement.encode()).hexdigest(),
        }},
    }, patch_plan, sequence=1)
    assert validate_worker_tool_receipts([write], patch_plan)[0]["path"] == "src/example.py"
    assert private_replacement not in json.dumps(write, sort_keys=True)

    execute_plan = build_plan_bundle(root, _spec())["plan"]
    execute = _execute_receipt(execute_plan, stdout="private-execute-output")
    assert validate_worker_tool_receipts([execute], execute_plan)[0]["success"] is True

    read_plan = build_plan_bundle(root, _spec(operations=[_read_operation()]))["plan"]
    read = build_worker_tool_receipt(_read_item(root), read_plan, sequence=1)
    files = [{
        "path": "src/example.py",
        "sha256": hashlib.sha256((root / "src/example.py").read_bytes()).hexdigest(),
        "size": (root / "src/example.py").stat().st_size,
    }]
    inventory_result = {
        "schema_version": "worker-result/1.0",
        "run_id": read_plan["run_id"],
        "run_plan_hash": read_plan["run_plan_hash"],
        "base_snapshot_hash": read_plan["snapshot_hash"],
        "files": files,
        "completed_operation_ids": [],
    }
    inventory_result["result_hash"] = artifact_hash(inventory_result)
    inventory = build_worker_tool_receipt({
        "id": "call_inventory_01", "type": "mcp_tool_call", "server": "ur_worker",
        "tool": "worker_inventory", "status": "completed", "arguments": {},
        "result": {"content": [], "structuredContent": inventory_result},
    }, read_plan, sequence=2)
    receipts = validate_worker_tool_receipts([read, inventory], read_plan)
    assert receipts[1]["file_count"] == 1


def test_codex_token_ceiling_exposes_only_bounded_closed_diagnostics(tmp_path: Path) -> None:
    root = _project(tmp_path)
    spec = _spec()
    spec["resources"]["max_total_tokens"] = 8
    plan = build_plan_bundle(root, spec)["plan"]
    raw_final = json.dumps({"segments": [], "private": "raw-final-must-not-persist"})
    events = [
        {"type": "item.completed", "item": {"type": "reasoning", "text": "private reasoning"}},
        {"type": "item.completed", "item": {
            "type": "mcp_tool_call", "server": "ur_worker", "tool": "worker_read",
            "arguments": {"private": "argument-must-not-persist"},
        }},
        {"type": "item.completed", "item": {
            "type": "mcp_tool_call", "server": "private-server", "tool": "private-tool-name",
        }},
        {"type": "item.completed", "item": {"type": "private-item-type"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": raw_final}},
        {"type": "turn.completed", "usage": {
            "input_tokens": 9, "cached_input_tokens": 2, "output_tokens": 1,
            "reasoning_output_tokens": 1,
        }},
    ]
    stdout = "\n".join(json.dumps(event) for event in events)
    runner = CodexRunner(
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    )

    with pytest.raises(CodexTokenCeilingError) as caught:
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

    diagnostic = caught.value.diagnostic
    rendered = json.dumps(diagnostic, sort_keys=True)
    assert diagnostic["approved_max_total_tokens"] == 8
    assert diagnostic["usage"] == {
        "input_tokens": 9, "cached_input_tokens": 2, "output_tokens": 1,
        "reasoning_output_tokens": 1, "total_tokens": 10,
    }
    assert diagnostic["event_count"] == 6
    assert diagnostic["event_item_type_counts"] == {
        "agent_message": 1, "mcp_tool_call": 2, "reasoning": 1,
    }
    assert diagnostic["worker_tool_call_counts"]["worker_read"] == 1
    assert diagnostic["unknown_item_type_count"] == 1
    assert diagnostic["unknown_mcp_tool_call_count"] == 1
    assert diagnostic["events_hash"] == artifact_hash(events)
    assert diagnostic["final_text_sha256"] == (
        "sha256:" + hashlib.sha256(raw_final.encode()).hexdigest()
    )
    assert diagnostic["eligible"] is False
    assert "raw-final-must-not-persist" not in rendered
    assert "private reasoning" not in rendered
    assert "argument-must-not-persist" not in rendered
    assert "private-server" not in rendered
    assert "private-tool-name" not in rendered
    assert "private-item-type" not in rendered


def test_token_ceiling_persists_only_ineligible_failure_and_blocks_replay(tmp_path: Path) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    bundle = build_plan_bundle(root, _spec())
    approval_store = HarnessApprovalStore(root, state_root=state)
    approval_store.create(
        bundle["plan"],
        expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    raw_final = '{"segments":[],"private":"controller-secret-must-not-persist"}'
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": raw_final}},
        {"type": "turn.completed", "usage": {"input_tokens": 100001, "output_tokens": 1}},
    ]

    class Runner:
        def run(self, plan, **_kwargs):
            approval_store.verify_consumed(plan)
            raise CodexTokenCeilingError(
                approved_max_total_tokens=plan["resources"]["max_total_tokens"],
                usage={
                    "input_tokens": 100001, "cached_input_tokens": 0,
                    "output_tokens": 1, "reasoning_output_tokens": 0,
                },
                events=events,
                final_text=raw_final,
            )

    with pytest.raises(CodexTokenCeilingError):
        execute_codex(
            root,
            bundle,
            prompt="Run only the sealed operation.",
            state_root=state,
            runner=Runner(),
        )

    run = HarnessRunStore(root, state_root=state).run_dir(bundle["plan"]["run_id"])
    assert not (run / "result.json").exists()
    failure = json.loads((run / "failure.json").read_text(encoding="utf-8"))
    serialized = json.dumps(failure, sort_keys=True)
    assert failure["failure_class"] == "token_ceiling_exceeded"
    assert failure["eligible"] is False
    assert failure["diagnostic"]["usage"]["total_tokens"] == 100002
    assert failure["failure_hash"] == artifact_hash({
        key: value for key, value in failure.items() if key != "failure_hash"
    })
    assert failure["agent_creation_approval_consumption_hash"].startswith("sha256:")
    assert "controller-secret-must-not-persist" not in serialized
    with pytest.raises(HarnessApprovalError, match="already consumed"):
        execute_codex(
            root,
            bundle,
            prompt="Run only the sealed operation.",
            state_root=state,
            runner=Runner(),
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


def test_only_attested_benchmark_results_are_canonical_promotion_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path)
    initialize_project(root)
    append_record(root, {
        "schema_version": "core/1.0", "record_id": "approval_benchmark",
        "record_kind": "approval", "study_id": "study_benchmark",
        "occurred_at": "2026-08-14T00:00:00+00:00",
        "recorded_at": "2026-08-14T00:00:00+00:00", "status": "approved",
        "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
        "payload": {"scope": {"study_ids": ["study_benchmark"], "record_kinds": ["observation"]}},
    }, approval_bootstrap=True)
    state = tmp_path / "host-state"
    monkeypatch.setenv("UNIVERSAL_RESEARCH_HARNESS_STATE_ROOT", str(state))
    bundle = build_plan_bundle(root, _spec(workflow_mode="benchmark", verification_mode="strict"))
    store = HarnessRunStore(root, state_root=state)
    run = store.create(bundle, "Report the benchmark only through verified segments.")
    segment = {
        "claim_id": "benchmark_claim", "text": "The benchmark result is verified.",
        "kind": "result", "final": True, "external": False, "numerical": False,
        "citation": True, "benchmark": True, "causal": False, "canonical": False,
        "conflicting": False, "evidence_refs": ["artifact_benchmark"],
    }
    result = {
        "schema_version": "secure-harness-codex-result/2.0", "run_id": "run_secure_01",
        "run_plan_hash": bundle["plan"]["run_plan_hash"], "model": bundle["plan"]["model"],
        "workflow_mode": "benchmark", "usage": {
            "input_tokens": 1, "cached_input_tokens": 0,
            "output_tokens": 1, "reasoning_output_tokens": 0,
            "total_tokens": 2,
        },
        "events_hash": "sha256:" + "a" * 64,
        "tool_receipts": [_execute_receipt(bundle["plan"])],
        "structured_output": {"segments": [segment]},
        "agent_creation_approval_consumption_hash": "sha256:" + "b" * 64,
        "executed": True,
    }
    result["result_hash"] = artifact_hash(result)
    store.write_result(run, result)
    receipt = {
        "claim_id": "benchmark_claim", "evidence_refs": ["artifact_benchmark"],
        "retrieval_passed": True, "source_verification_passed": True, "independent_review_passed": True,
    }
    receipt["receipt_hash"] = artifact_hash(receipt)
    review = review_run(root, "run_secure_01", receipts_path=None, state_root=state)
    assert review["status"] == "blocked"
    receipts_path = tmp_path / "receipts.json"
    receipts_path.write_text(json.dumps([receipt]), encoding="utf-8")
    review = review_run(root, "run_secure_01", receipts_path=receipts_path, state_root=state)
    attested = attest_run(
        root, "run_secure_01", expected_review_hash=artifact_hash(review),
        receipts_path=receipts_path, state_root=state,
    )
    binding = {key: attested[key] for key in (
        "run_id", "workflow_mode", "run_plan_hash", "result_hash", "attestation_hash",
    )}
    record = {
        "schema_version": "core/1.0", "record_id": "observation_benchmark",
        "record_kind": "observation", "study_id": "study_benchmark",
        "occurred_at": "2026-08-14T00:01:00+00:00",
        "recorded_at": "2026-08-14T00:01:00+00:00", "status": "completed",
        "created_by": {"actor_id": "actor_researcher", "actor_type": "ai"},
        "approval_refs": ["approval_benchmark"],
        "payload": {"workflow_mode": "benchmark", "harness_attestation": binding},
    }
    assert validate_candidate_records(root, [record]) == []
    missing = {**record, "record_id": "observation_unattested", "payload": {"workflow_mode": "benchmark"}}
    assert "requires a valid secure-harness attestation" in validate_candidate_records(root, [missing])[0].message
    forged = {**record, "record_id": "observation_forged", "payload": {
        "workflow_mode": "benchmark", "harness_attestation": {**binding, "result_hash": "sha256:" + "0" * 64},
    }}
    assert "requires a valid secure-harness attestation" in validate_candidate_records(root, [forged])[0].message
    mismatched_mode = {**record, "record_id": "observation_mode_mismatch", "payload": {
        "workflow_mode": "final_review", "harness_attestation": binding,
    }}
    assert "requires a valid secure-harness attestation" in validate_candidate_records(root, [mismatched_mode])[0].message


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


def test_test_operation_requires_exactly_one_source_contract(tmp_path: Path) -> None:
    root = _project(tmp_path)
    spec = _spec()
    spec.pop("test_contracts")
    with pytest.raises(HarnessContractError, match="exactly one test contract"):
        build_plan_bundle(root, spec)
    spec = _spec()
    spec["test_contracts"].append(json.loads(json.dumps(spec["test_contracts"][0])))
    with pytest.raises(HarnessContractError, match="exactly one test contract"):
        build_plan_bundle(root, spec)


def test_test_contract_rejects_a_guessed_python_symbol_before_plan_creation(tmp_path: Path) -> None:
    root = _project(tmp_path)
    spec = _spec()
    spec["test_contracts"][0]["checks"][0]["selector"] = "build_evaluator_command"
    with pytest.raises(HarnessContractError, match="Python symbol does not exist"):
        build_plan_bundle(root, spec)


def test_test_contract_supports_exact_assignment_literal_and_jsonl_checks(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "data").mkdir()
    (root / "data/tasks.jsonl").write_text(
        '{"source_bundle_id":"bundle_v1"}\n{"source_bundle_id":"bundle_v1"}\n',
        encoding="utf-8",
    )
    spec = _spec()
    spec["operations"][0]["paths"].append("data/tasks.jsonl")
    spec["agent_creation_disclosure"]["scope"]["paths"].append("data/tasks.jsonl")
    spec["agent_creation_disclosure"]["scope"]["paths"].sort()
    spec["test_contracts"][0]["checks"] = [
        {
            "check_id": "source_value_assignment",
            "path": "src/example.py",
            "kind": "python_assignment",
            "selector": "VALUE",
            "expected": 1,
        },
        {
            "check_id": "source_value_literal",
            "path": "src/example.py",
            "kind": "python_literal",
            "selector": "VALUE = 1",
            "expected": True,
        },
        {
            "check_id": "task_source_bundle",
            "path": "data/tasks.jsonl",
            "kind": "jsonl_key",
            "selector": "source_bundle_id",
            "expected": "bundle_v1",
        },
    ]
    spec["test_contracts"][0]["checks"][1]["selector"] = 1
    with pytest.raises(HarnessContractError, match="selector is invalid"):
        build_plan_bundle(root, spec)
    spec["test_contracts"][0]["checks"][1]["selector"] = "VALUE = 1"
    spec["test_contracts"][0]["checks"][1]["kind"] = "python_literal"
    with pytest.raises(HarnessContractError, match="Python literal does not exist"):
        build_plan_bundle(root, spec)
    spec["test_contracts"][0]["checks"].pop(1)
    bundle = build_plan_bundle(root, spec)
    assert verify_test_contracts(
        root, bundle["plan"]["test_contracts"], bundle["plan"]["operations"],
    )[0]["contract_hash"].startswith("sha256:")


def test_test_contract_drift_blocks_before_approval_consumption_or_runner(tmp_path: Path) -> None:
    root = _project(tmp_path)
    state = tmp_path / "state"
    bundle = build_plan_bundle(root, _spec())
    approval_store = HarnessApprovalStore(root, state_root=state)
    approval_store.create(
        bundle["plan"],
        expected_plan_hash=bundle["plan"]["run_plan_hash"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    (root / "src/example.py").write_text("VALUE = 2\n", encoding="utf-8")
    calls = 0

    class Runner:
        def run(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("runner must not be called")

    with pytest.raises(HarnessContractError, match="source hash mismatch"):
        execute_codex(
            root,
            bundle,
            prompt="Run only the sealed operation.",
            state_root=state,
            runner=Runner(),
        )
    assert calls == 0
    assert not approval_store._path(bundle["plan"]["run_id"], "consumed").exists()
