"""Host-owned orchestration for sealed Codex/Docker research runs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping

from universal_research_mcp.governance.hashing import artifact_hash

from .approval import HarnessApprovalStore, project_root_hash
from .claims import evaluate_segments
from .codex_runner import (
    CodexRunner,
    CodexTokenCeilingError,
    validate_worker_tool_receipts,
    write_output_schema,
)
from .contracts import HarnessContractError, build_run_plan, validate_run_plan
from .docker_backend import doctor as docker_doctor, inspect_plan
from .snapshot import build_manifest
from .test_contracts import seal_test_contracts, verify_test_contracts


BUNDLE_VERSION = "research-run-plan-bundle/2.0"
ATTESTATION_VERSION = "secure-harness-attestation/1.0"
CODEX_RESULT_VERSION = "secure-harness-codex-result/2.0"
_MAX_JSON_BYTES = 16 * 1024 * 1024
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODEX_RESULT_KEYS = {
    "schema_version", "run_id", "run_plan_hash", "model", "workflow_mode", "usage",
    "events_hash", "tool_receipts", "structured_output",
    "agent_creation_approval_consumption_hash", "executed", "result_hash",
}


def _load_json(path: str | Path, label: str) -> Any:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > _MAX_JSON_BYTES:
        raise HarnessContractError(f"{label} file is unsafe")
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessContractError(f"{label} is unreadable JSON") from exc


def validate_codex_result_record(value: object, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed when a persisted model result lacks bounded MCP evidence."""
    normalized = validate_run_plan(plan)
    if not isinstance(value, Mapping) or set(value) != _CODEX_RESULT_KEYS:
        raise HarnessContractError("run result has an unsupported shape")
    result = dict(value)
    if result.get("schema_version") != CODEX_RESULT_VERSION:
        raise HarnessContractError("run result schema is unsupported")
    if result.get("result_hash") != artifact_hash({
        key: item for key, item in result.items() if key != "result_hash"
    }):
        raise HarnessContractError("run result integrity check failed")
    if (
        result.get("run_id") != normalized["run_id"]
        or result.get("run_plan_hash") != normalized["run_plan_hash"]
        or result.get("model") != normalized["model"]
        or result.get("workflow_mode") != normalized["workflow_mode"]
        or result.get("executed") is not True
    ):
        raise HarnessContractError("run result is not bound to the sealed plan")
    usage = result.get("usage")
    usage_keys = {
        "input_tokens", "cached_input_tokens", "output_tokens",
        "reasoning_output_tokens", "total_tokens",
    }
    if not isinstance(usage, Mapping) or set(usage) != usage_keys or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in usage.values()
    ):
        raise HarnessContractError("run result usage is malformed")
    if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
        raise HarnessContractError("run result token total is inconsistent")
    for name in ("events_hash", "agent_creation_approval_consumption_hash"):
        item = result.get(name)
        if not isinstance(item, str) or not _SHA256.fullmatch(item):
            raise HarnessContractError("run result contains an invalid binding hash")
    output = result.get("structured_output")
    if not isinstance(output, Mapping) or not isinstance(output.get("segments"), list):
        raise HarnessContractError("run result structured output is malformed")
    result["tool_receipts"] = validate_worker_tool_receipts(
        result.get("tool_receipts"), normalized,
    )
    return result


def _load_codex_result(run: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    return validate_codex_result_record(_load_json(run / "result.json", "run result"), plan)


def build_plan_bundle(root: str | Path, specification: Mapping[str, Any]) -> dict[str, Any]:
    project = Path(root).resolve(strict=True)
    allowed = {
        "run_id", "workflow_id", "model", "reasoning_effort", "workflow_mode", "verification_mode",
        "approval_mode", "agent_creation_disclosure", "image", "resources", "operations",
        "test_contracts", "created_at", "expires_at",
    }
    unknown = sorted(set(specification) - allowed)
    if unknown:
        raise HarnessContractError(f"plan specification contains unsupported fields: {', '.join(unknown)}")
    operations = specification.get("operations")
    if not isinstance(operations, list):
        raise HarnessContractError("plan specification requires operations")
    snapshot_paths: list[str] = []
    for operation in operations:
        if not isinstance(operation, Mapping) or not isinstance(operation.get("paths"), list):
            raise HarnessContractError("every operation requires paths")
        snapshot_paths.extend(operation["paths"])
    manifest = build_manifest(project, snapshot_paths)
    test_contracts = seal_test_contracts(
        project,
        specification.get("test_contracts", []),
        operations,
    )
    manifest_paths = {
        entry["path"] for entry in manifest["files"] if entry.get("absent") is not True
    }
    if any(
        source["path"] not in manifest_paths
        for contract in test_contracts
        for source in contract["sources"]
    ):
        raise HarnessContractError("test contract source is missing from the worker snapshot")
    plan_specification = {**dict(specification), "test_contracts": test_contracts}
    plan = build_run_plan({
        "schema_version": "research-run-plan/2.0",
        "workflow_mode": "lightweight",
        **plan_specification,
        "project_root_hash": project_root_hash(project),
        "snapshot_hash": manifest["snapshot_hash"],
    })
    bundle = {"schema_version": BUNDLE_VERSION, "plan": plan, "snapshot_manifest": manifest}
    bundle["bundle_hash"] = artifact_hash(bundle)
    return bundle


def load_bundle(path: str | Path) -> dict[str, Any]:
    value = _load_json(path, "run plan bundle")
    if not isinstance(value, dict) or set(value) != {"schema_version", "plan", "snapshot_manifest", "bundle_hash"}:
        raise HarnessContractError("run plan bundle has an unsupported shape")
    if value["schema_version"] != BUNDLE_VERSION:
        raise HarnessContractError("run plan bundle schema is unsupported")
    if value["bundle_hash"] != artifact_hash({key: item for key, item in value.items() if key != "bundle_hash"}):
        raise HarnessContractError("run plan bundle hash mismatch")
    plan = validate_run_plan(value["plan"])
    manifest = value["snapshot_manifest"]
    if not isinstance(manifest, dict) or manifest.get("snapshot_hash") != plan["snapshot_hash"]:
        raise HarnessContractError("bundle snapshot binding is invalid")
    return {**value, "plan": plan}


def preflight(root: str | Path, bundle: Mapping[str, Any], *, docker_runner=None) -> dict[str, Any]:
    project = Path(root).resolve(strict=True)
    plan = validate_run_plan(bundle.get("plan"))
    issues: list[dict[str, str]] = []
    if plan["approval_mode"] != "plan_once":
        issues.append({
            "code": "APPROVAL_MODE_UNAVAILABLE",
            "message": "this preview executes only the plan_once approval mode",
        })
    if plan["project_root_hash"] != project_root_hash(project):
        issues.append({"code": "PROJECT_BINDING", "message": "plan is bound to another project"})
    manifest = bundle.get("snapshot_manifest")
    paths = [entry.get("path") for entry in (manifest or {}).get("files", []) if isinstance(entry, dict)]
    try:
        current = build_manifest(project, paths)
        if current["snapshot_hash"] != plan["snapshot_hash"]:
            issues.append({"code": "SNAPSHOT_DRIFT", "message": "project content changed after planning"})
    except HarnessContractError as exc:
        issues.append({"code": "SNAPSHOT_INVALID", "message": str(exc)})
    try:
        verified_contracts = verify_test_contracts(
            project,
            plan["test_contracts"],
            plan["operations"],
        )
    except HarnessContractError as exc:
        verified_contracts = []
        issues.append({"code": "TEST_CONTRACT_INVALID", "message": str(exc)})
    docker = docker_doctor(runner=docker_runner)
    if docker["status"] != "ready":
        issues.append({"code": "DOCKER_UNAVAILABLE", "message": "Docker CLI or daemon is unavailable"})
    plan_checks = inspect_plan(plan, runner=docker_runner)
    if not plan_checks["ok"]:
        issues.append({"code": "WORKER_RUNTIME_UNAVAILABLE", "message": "pinned image or requested GPU runtime is unavailable"})
    return {
        "schema_version": "secure-harness-preflight/1.0",
        "valid": not issues,
        "issues": issues,
        "run_id": plan["run_id"],
        "run_plan_hash": plan["run_plan_hash"],
        "snapshot_hash": plan["snapshot_hash"],
        "model": plan["model"],
        "reasoning_effort": plan["reasoning_effort"],
        "approval_mode": plan["approval_mode"],
        "test_contract_hashes": [item["contract_hash"] for item in verified_contracts],
        "resources": plan["resources"],
        "docker": docker,
        "worker_runtime": plan_checks,
        "executed": False,
    }


class HarnessRunStore:
    def __init__(self, project_root: str | Path, *, state_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        base = Path(state_root) if state_root is not None else Path(
            os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")
        )
        base = Path(os.path.abspath(os.fspath(base.expanduser())))
        if not base.is_absolute() or base.resolve(strict=False) != base:
            raise HarnessContractError("run state root must be absolute and symlink-free")
        try:
            base.relative_to(self.project_root)
        except ValueError:
            pass
        else:
            raise HarnessContractError("run state cannot be inside the project")
        digest = project_root_hash(self.project_root).removeprefix("sha256:")
        self.root = base / "universal-research-mcp" / "harness-runs" / digest

    def create(self, bundle: Mapping[str, Any], prompt: str) -> Path:
        plan = validate_run_plan(bundle.get("plan"))
        run = self.root / plan["run_id"]
        self._ensure_safe_root()
        try:
            run.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise HarnessContractError("run state already exists") from exc
        self._create_json(run / "plan.json", plan)
        self._create_json(run / "manifest.json", bundle["snapshot_manifest"])
        self._create_json(run / "request.json", {
            "schema_version": "harness-request/1.0",
            "run_id": plan["run_id"],
            "run_plan_hash": plan["run_plan_hash"],
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        write_output_schema(run / "output-schema.json")
        return run

    def _ensure_safe_root(self) -> None:
        current = Path(self.root.anchor)
        for component in self.root.parts[1:]:
            current /= component
            if current.exists() or current.is_symlink():
                if current.is_symlink() or not current.is_dir():
                    raise HarnessContractError("run state path is unsafe")
            else:
                current.mkdir(mode=0o700)

    @staticmethod
    def _create_json(path: Path, value: Mapping[str, Any]) -> None:
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def write_result(self, run: Path, value: Mapping[str, Any]) -> None:
        self._create_json(run / "result.json", value)

    def write_failure(self, run: Path, value: Mapping[str, Any]) -> None:
        self._create_json(run / "failure.json", value)

    def write_attestation(self, run: Path, value: Mapping[str, Any]) -> None:
        self._create_json(run / "attestation.json", value)

    def run_dir(self, run_id: str) -> Path:
        if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in run_id):
            raise HarnessContractError("run ID is invalid")
        candidate = self.root / run_id
        if candidate.is_symlink() or not candidate.is_dir():
            raise HarnessContractError("run state does not exist")
        return candidate


def execute_codex(
    root: str | Path,
    bundle: Mapping[str, Any],
    *,
    prompt: str,
    state_root: str | Path | None = None,
    runner: CodexRunner | None = None,
) -> dict[str, Any]:
    project = Path(root).resolve(strict=True)
    plan = validate_run_plan(bundle.get("plan"))
    verify_test_contracts(project, plan["test_contracts"], plan["operations"])
    approval_store = HarnessApprovalStore(project, state_root=state_root)
    consumption = approval_store.consume(plan)
    store = HarnessRunStore(project, state_root=state_root)
    run = store.create(bundle, prompt)
    workspace = run / "workspace"
    try:
        result = (runner or CodexRunner()).run(
            plan,
            prompt=prompt,
            control_root=run,
            project_root=project,
            plan_path=run / "plan.json",
            manifest_path=run / "manifest.json",
            workspace_path=workspace,
            schema_path=run / "output-schema.json",
            approval_state_root=approval_store.state_root,
        )
    except CodexTokenCeilingError as exc:
        failure = {
            "schema_version": "secure-harness-codex-failure/1.0",
            "run_id": plan["run_id"],
            "run_plan_hash": plan["run_plan_hash"],
            "model": plan["model"],
            "workflow_mode": plan["workflow_mode"],
            "failure_class": "token_ceiling_exceeded",
            "diagnostic": exc.diagnostic,
            "agent_creation_approval_consumption_hash": consumption["consumption_hash"],
            "executed": True,
            "eligible": False,
        }
        failure["failure_hash"] = artifact_hash(failure)
        store.write_failure(run, failure)
        raise
    tool_receipts = validate_worker_tool_receipts(result.tool_receipts, plan)
    record = {
        "schema_version": CODEX_RESULT_VERSION,
        "run_id": plan["run_id"],
        "run_plan_hash": plan["run_plan_hash"],
        "model": result.model,
        "workflow_mode": plan["workflow_mode"],
        "usage": result.usage,
        "events_hash": result.events_hash,
        "tool_receipts": tool_receipts,
        "structured_output": result.final_output,
        "agent_creation_approval_consumption_hash": consumption["consumption_hash"],
        "executed": True,
    }
    record["result_hash"] = artifact_hash(record)
    record = validate_codex_result_record(record, plan)
    store.write_result(run, record)
    return {key: record[key] for key in (
        "schema_version", "run_id", "run_plan_hash", "model", "usage", "events_hash",
        "tool_receipts", "result_hash", "agent_creation_approval_consumption_hash", "executed",
    )}


def review_run(
    root: str | Path,
    run_id: str,
    *,
    receipts_path: str | Path | None = None,
    state_root: str | Path | None = None,
) -> dict[str, Any]:
    run = HarnessRunStore(root, state_root=state_root).run_dir(run_id)
    plan = validate_run_plan(_load_json(run / "plan.json", "run plan"))
    result = _load_codex_result(run, plan)
    receipts = [] if receipts_path is None else _load_json(receipts_path, "verification receipts")
    if not isinstance(receipts, list):
        raise HarnessContractError("verification receipts must be an array")
    reviewed = evaluate_segments(
        result.get("structured_output", {}).get("segments"),
        verification_mode=plan["verification_mode"],
        verification_receipts=receipts,
    )
    return {
        **reviewed,
        "run_id": run_id,
        "run_plan_hash": plan["run_plan_hash"],
        "usage": result.get("usage"),
        "model": result.get("model"),
    }


def _attestation_binding(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        "run_id": str(value["run_id"]),
        "workflow_mode": str(value["workflow_mode"]),
        "run_plan_hash": str(value["run_plan_hash"]),
        "result_hash": str(value["result_hash"]),
        "attestation_hash": str(value["attestation_hash"]),
    }


def attest_run(
    root: str | Path,
    run_id: str,
    *,
    expected_review_hash: str,
    receipts_path: str | Path | None = None,
    state_root: str | Path | None = None,
) -> dict[str, Any]:
    """Create a one-time promotion attestation for a reviewed governed run."""

    store = HarnessRunStore(root, state_root=state_root)
    run = store.run_dir(run_id)
    plan = validate_run_plan(_load_json(run / "plan.json", "run plan"))
    mode = plan["workflow_mode"]
    if mode not in {"benchmark", "final_review"}:
        raise HarnessContractError("only benchmark or final_review runs may be attested for promotion")
    review = review_run(root, run_id, receipts_path=receipts_path, state_root=state_root)
    review_hash = artifact_hash(review)
    if expected_review_hash != review_hash:
        raise HarnessContractError("expected review hash does not match the reviewed run")
    if review.get("status") != "passed" or review.get("claim_eligibility") != "eligible":
        raise HarnessContractError("blocked review cannot be attested for canonical promotion")
    result = _load_codex_result(run, plan)
    result_hash = result.get("result_hash")
    if not isinstance(result_hash, str) or result_hash != artifact_hash({
        key: item for key, item in result.items() if key != "result_hash"
    }):
        raise HarnessContractError("run result integrity check failed")
    attestation = {
        "schema_version": ATTESTATION_VERSION,
        "project_root_hash": plan["project_root_hash"],
        "run_id": plan["run_id"],
        "workflow_mode": mode,
        "run_plan_hash": plan["run_plan_hash"],
        "result_hash": result_hash,
        "review_hash": review_hash,
        "claim_eligibility": "eligible",
        "attested_at": datetime.now(timezone.utc).isoformat(),
    }
    attestation["attestation_hash"] = artifact_hash(attestation)
    store.write_attestation(run, attestation)
    return {"schema_version": ATTESTATION_VERSION, "status": "attested", **_attestation_binding(attestation)}


def promotion_attestation_binding(
    root: str | Path,
    value: object,
    *,
    state_root: str | Path | None = None,
) -> dict[str, str]:
    """Verify the exact persisted harness attestation a canonical record cites."""

    if not isinstance(value, Mapping) or set(value) != {
        "run_id", "workflow_mode", "run_plan_hash", "result_hash", "attestation_hash",
    }:
        raise HarnessContractError("harness_attestation must be an exact promotion binding")
    run_id = value.get("run_id")
    if not isinstance(run_id, str):
        raise HarnessContractError("harness_attestation run_id is invalid")
    store = HarnessRunStore(root, state_root=state_root)
    run = store.run_dir(run_id)
    attestation = _load_json(run / "attestation.json", "harness attestation")
    expected_keys = {
        "schema_version", "project_root_hash", "run_id", "workflow_mode", "run_plan_hash",
        "result_hash", "review_hash", "claim_eligibility", "attested_at", "attestation_hash",
    }
    if set(attestation) != expected_keys or attestation.get("schema_version") != ATTESTATION_VERSION:
        raise HarnessContractError("harness attestation schema is invalid")
    if attestation.get("attestation_hash") != artifact_hash({
        key: item for key, item in attestation.items() if key != "attestation_hash"
    }):
        raise HarnessContractError("harness attestation integrity check failed")
    plan = validate_run_plan(_load_json(run / "plan.json", "run plan"))
    result = _load_codex_result(run, plan)
    if plan["workflow_mode"] not in {"benchmark", "final_review"}:
        raise HarnessContractError("harness attestation is not a promotion mode")
    if (
        attestation.get("run_id") != plan["run_id"]
        or attestation.get("workflow_mode") != plan["workflow_mode"]
        or attestation.get("run_plan_hash") != plan["run_plan_hash"]
        or result.get("run_id") != plan["run_id"]
        or result.get("run_plan_hash") != plan["run_plan_hash"]
        or result.get("result_hash") != attestation.get("result_hash")
        or attestation.get("claim_eligibility") != "eligible"
    ):
        raise HarnessContractError("harness result no longer matches its attestation")
    if attestation.get("project_root_hash") != project_root_hash(root):
        raise HarnessContractError("harness attestation is bound to another project")
    binding = _attestation_binding(attestation)
    if dict(value) != binding:
        raise HarnessContractError("harness attestation binding does not match")
    return binding


def change_review(root: str | Path, run_id: str, *, state_root: str | Path | None = None) -> dict[str, Any]:
    project = Path(root).resolve(strict=True)
    run = HarnessRunStore(project, state_root=state_root).run_dir(run_id)
    plan = validate_run_plan(_load_json(run / "plan.json", "run plan"))
    manifest = _load_json(run / "manifest.json", "snapshot manifest")
    workspace = run / "workspace"
    allowed_roots = {
        path
        for operation in plan["operations"] if operation["kind"] == "patch"
        for path in operation["paths"]
    }
    def authorized(path: str) -> bool:
        candidate = Path(path)
        return any(candidate == Path(root) or Path(root) in candidate.parents for root in allowed_roots)

    changes: list[dict[str, Any]] = []
    entries = {entry["path"]: entry for entry in manifest.get("files", [])}
    workspace_files = {
        item.relative_to(workspace).as_posix(): item
        for item in workspace.rglob("*") if item.is_file() and not item.is_symlink()
    }
    for path in sorted(set(entries) | set(workspace_files)):
        entry = entries.get(path, {"path": path, "sha256": None})
        path = entry["path"]
        source = project / path
        staged = workspace / path
        current = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() and not source.is_symlink() else None
        if current != entry["sha256"]:
            raise HarnessContractError("project changed since the approved snapshot")
        staged_hash = hashlib.sha256(staged.read_bytes()).hexdigest() if staged.is_file() and not staged.is_symlink() else None
        if staged_hash != entry["sha256"]:
            if not authorized(path):
                raise HarnessContractError("worker modified a path without patch authority")
            if staged_hash is None:
                raise HarnessContractError("file deletion is unsupported in this preview")
            changes.append({"path": path, "before_sha256": entry["sha256"], "after_sha256": staged_hash})
    report = {"schema_version": "harness-change-review/1.0", "run_id": run_id, "changes": changes}
    report["diff_hash"] = artifact_hash(report)
    return report


def apply_changes(
    root: str | Path,
    run_id: str,
    *,
    confirm_diff_hash: str,
    state_root: str | Path | None = None,
) -> dict[str, Any]:
    project = Path(root).resolve(strict=True)
    store = HarnessRunStore(project, state_root=state_root)
    run = store.run_dir(run_id)
    review = change_review(project, run_id, state_root=state_root)
    if review["diff_hash"] != confirm_diff_hash:
        raise HarnessContractError("confirmed diff hash does not match current review")
    if (run / "applied.json").exists():
        raise HarnessContractError("run changes were already applied")
    for change in review["changes"]:
        destination = project / change["path"]
        source = run / "workspace" / change["path"]
        if destination.is_symlink() or source.is_symlink() or not source.is_file():
            raise HarnessContractError("change contains an unsafe file type")
        temporary = destination.with_name(destination.name + ".ur-apply-tmp")
        if temporary.exists():
            raise HarnessContractError("apply temporary path already exists")
        shutil.copyfile(source, temporary, follow_symlinks=False)
        temporary.replace(destination)
    record = {
        "schema_version": "harness-change-application/1.0",
        "run_id": run_id,
        "diff_hash": review["diff_hash"],
        "applied_paths": [item["path"] for item in review["changes"]],
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    store._create_json(run / "applied.json", record)
    return record
