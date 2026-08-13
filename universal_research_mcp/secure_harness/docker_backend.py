"""Deterministic Docker command construction and bounded execution."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping

from universal_research_mcp.governance.hashing import artifact_hash

from .contracts import HarnessContractError, validate_run_plan


@dataclass(frozen=True)
class DockerExecutionResult:
    operation_id: str
    exit_code: int
    stdout: str
    stderr: str
    command_hash: str


def docker_command(plan: Mapping[str, Any], operation_id: str, snapshot_root: str | Path) -> list[str]:
    normalized = validate_run_plan(plan)
    operation = next((item for item in normalized["operations"] if item["operation_id"] == operation_id), None)
    if operation is None:
        raise HarnessContractError("operation is not in the sealed plan")
    if operation["kind"] not in {"test", "build", "experiment"}:
        raise HarnessContractError("operation kind is not a container recipe")
    snapshot = Path(snapshot_root).resolve(strict=True)
    command = [
        "docker", "run", "--rm", "--init", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", str(normalized["resources"]["pids"]),
        "--cpus", str(normalized["resources"]["cpus"]),
        "--memory", f"{normalized['resources']['memory_mb']}m",
        "--memory-swap", f"{normalized['resources']['memory_mb']}m",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--mount", f"type=bind,src={snapshot},dst=/workspace,rw",
        "--workdir", f"/workspace/{operation['cwd']}",
    ]
    for name, value in sorted(operation["environment"].items()):
        command.extend(("--env", f"{name}={value}"))
    gpu_devices = operation["gpu_devices"]
    if gpu_devices:
        command.extend(("--gpus", "device=" + ",".join(gpu_devices)))
        command.extend(("--env", "NVIDIA_DRIVER_CAPABILITIES=compute,utility"))
    command.append(normalized["image"])
    command.extend(operation["argv"])
    return command


class DockerBackend:
    def __init__(self, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self._runner = runner or subprocess.run

    def execute(self, plan: Mapping[str, Any], operation_id: str, snapshot_root: str | Path) -> DockerExecutionResult:
        normalized = validate_run_plan(plan)
        operation = next(item for item in normalized["operations"] if item["operation_id"] == operation_id)
        command = docker_command(normalized, operation_id, snapshot_root)
        completed = self._runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=operation["timeout_seconds"],
            check=False,
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        stdout = completed.stdout[-1024 * 1024:]
        stderr = completed.stderr[-1024 * 1024:]
        return DockerExecutionResult(
            operation_id=operation_id,
            exit_code=int(completed.returncode),
            stdout=stdout,
            stderr=stderr,
            command_hash=artifact_hash({"argv": command}),
        )


def doctor(*, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> dict[str, Any]:
    execute = runner or subprocess.run
    checks: dict[str, Any] = {}
    for name, command in {
        "docker_cli": ["docker", "--version"],
        "docker_daemon": ["docker", "version", "--format", "{{.Server.Version}}"],
    }.items():
        try:
            result = execute(command, capture_output=True, text=True, timeout=10, check=False)
            checks[name] = {"ok": result.returncode == 0, "detail": (result.stdout or result.stderr).strip()[:200]}
        except (OSError, subprocess.SubprocessError) as exc:
            checks[name] = {"ok": False, "detail": type(exc).__name__}
    checks["isolation_contract"] = {
        "ok": True,
        "network_default": "none",
        "capabilities": "drop_all",
        "root_filesystem": "read_only",
        "docker_socket": "never_mounted",
        "rootless_daemon_required": False,
        "rootless_daemon_recommended": True,
    }
    return {
        "schema_version": "secure-harness-doctor/1.0",
        "status": "ready" if all(item["ok"] for item in checks.values()) else "blocked",
        "checks": checks,
        "executed_worker": False,
    }


def inspect_plan(plan: Mapping[str, Any], *, runner=None) -> dict[str, Any]:
    """Verify that a sealed image and any requested GPU runtime exist without pulling."""
    normalized = validate_run_plan(plan)
    execute = runner or subprocess.run
    checks: dict[str, Any] = {}
    try:
        image = execute(
            ["docker", "image", "inspect", normalized["image"]],
            capture_output=True, text=True, timeout=10, check=False,
        )
        checks["pinned_image_local"] = {"ok": image.returncode == 0}
    except (OSError, subprocess.SubprocessError):
        checks["pinned_image_local"] = {"ok": False}
    needs_gpu = any(operation["gpu_devices"] for operation in normalized["operations"])
    if needs_gpu:
        try:
            runtime = execute(
                ["docker", "info", "--format", "{{json .Runtimes}}"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            checks["nvidia_runtime"] = {
                "ok": runtime.returncode == 0 and '"nvidia"' in runtime.stdout,
            }
        except (OSError, subprocess.SubprocessError):
            checks["nvidia_runtime"] = {"ok": False}
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks}
