"""Stateful, plan-bound worker operations exposed through the execution MCP."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from universal_research_mcp.governance.hashing import artifact_hash

from .approval import HarnessApprovalStore
from .contracts import HarnessContractError, load_run_plan
from .docker_backend import DockerBackend
from .snapshot import materialize_snapshot


MAX_TOOL_TEXT_BYTES = 1024 * 1024


class WorkerSession:
    """Verify pre-consumed approval, then expose only sealed operations."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        plan_path: str | Path,
        manifest_path: str | Path,
        workspace: str | Path,
        approval_store: HarnessApprovalStore | None = None,
        backend: DockerBackend | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.plan = load_run_plan(plan_path)
        self.manifest = self._manifest(manifest_path)
        if self.manifest.get("snapshot_hash") != self.plan["snapshot_hash"]:
            raise HarnessContractError("plan and snapshot manifest hashes differ")
        self.workspace = materialize_snapshot(self.project_root, self.manifest, workspace)
        self.backend = backend or DockerBackend()
        self.approval_store = approval_store or HarnessApprovalStore(self.project_root)
        self.consumption = self.approval_store.verify_consumed(self.plan)
        self._completed: set[str] = set()

    @staticmethod
    def _manifest(path: str | Path) -> dict[str, Any]:
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 16 * 1024 * 1024:
            raise HarnessContractError("snapshot manifest file is unsafe")
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HarnessContractError("snapshot manifest is unreadable") from exc
        if not isinstance(value, dict):
            raise HarnessContractError("snapshot manifest must be an object")
        return value

    def _operation(self, operation_id: str, kind: str) -> dict[str, Any]:
        operation = next((item for item in self.plan["operations"] if item["operation_id"] == operation_id), None)
        if operation is None or operation["kind"] != kind:
            raise HarnessContractError("tool call does not match a sealed operation")
        return operation

    def _file(self, operation: dict[str, Any], path: str) -> Path:
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts:
            raise HarnessContractError("path is not authorized by this operation")
        requested = Path(*pure.parts)
        allowed = any(
            requested == Path(root) or Path(root) in requested.parents
            for root in operation["paths"]
        )
        if not allowed:
            raise HarnessContractError("path is not authorized by this operation")
        candidate = self.workspace / Path(*pure.parts)
        try:
            resolved_parent = candidate.parent.resolve(strict=True)
            resolved_parent.relative_to(self.workspace)
        except (OSError, ValueError) as exc:
            raise HarnessContractError("worker path escapes its snapshot") from exc
        if candidate.is_symlink():
            raise HarnessContractError("worker paths cannot be symlinks")
        return candidate

    def read(self, operation_id: str, path: str, start_line: int, end_line: int) -> dict[str, Any]:
        operation = self._operation(operation_id, "read")
        candidate = self._file(operation, path)
        if not candidate.is_file() or candidate.stat().st_size > MAX_TOOL_TEXT_BYTES:
            raise HarnessContractError("read target is unavailable or too large")
        if not isinstance(start_line, int) or not isinstance(end_line, int) or start_line < 1 or end_line < start_line or end_line - start_line > 500:
            raise HarnessContractError("line range is invalid or too large")
        lines = candidate.read_text(encoding="utf-8").splitlines()
        content = "\n".join(lines[start_line - 1:end_line])
        return {
            "operation_id": operation_id,
            "path": path,
            "start_line": start_line,
            "end_line": min(end_line, len(lines)),
            "content": content,
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        }

    def search(self, operation_id: str, query: str, limit: int = 20) -> dict[str, Any]:
        operation = self._operation(operation_id, "search")
        if not isinstance(query, str) or not query or len(query) > 500:
            raise HarnessContractError("search query is invalid")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise HarnessContractError("search limit is invalid")
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches: list[dict[str, Any]] = []
        for path in operation["paths"]:
            candidate = self._file(operation, path)
            if not candidate.is_file() or candidate.stat().st_size > MAX_TOOL_TEXT_BYTES:
                continue
            try:
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, 1):
                if pattern.search(line):
                    matches.append({"path": path, "line": number, "text": line[:1000]})
                    if len(matches) >= limit:
                        return {"operation_id": operation_id, "matches": matches, "truncated": True}
        return {"operation_id": operation_id, "matches": matches, "truncated": False}

    def write(self, operation_id: str, path: str, expected_sha256: str, content: str) -> dict[str, Any]:
        operation = self._operation(operation_id, "patch")
        candidate = self._file(operation, path)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_TOOL_TEXT_BYTES:
            raise HarnessContractError("replacement content is too large")
        current = "absent" if not candidate.exists() else hashlib.sha256(candidate.read_bytes()).hexdigest()
        if current != expected_sha256:
            raise HarnessContractError("patch base hash mismatch")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        temporary = candidate.with_name(candidate.name + ".ur-tmp")
        if temporary.exists():
            raise HarnessContractError("patch temporary path already exists")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(candidate)
        return {"operation_id": operation_id, "path": path, "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}

    def execute(self, operation_id: str) -> dict[str, Any]:
        if operation_id in self._completed:
            raise HarnessContractError("operation has already been executed")
        result = self.backend.execute(self.plan, operation_id, self.workspace)
        self._completed.add(operation_id)
        return {
            "operation_id": operation_id,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command_hash": result.command_hash,
            "success": result.exit_code == 0,
        }

    def inventory(self) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for candidate in sorted(self.workspace.rglob("*")):
            if candidate.is_symlink():
                raise HarnessContractError("worker created a symlink")
            if candidate.is_file():
                relative = candidate.relative_to(self.workspace).as_posix()
                files.append({
                    "path": relative,
                    "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "size": candidate.stat().st_size,
                })
        report = {
            "schema_version": "worker-result/1.0",
            "run_id": self.plan["run_id"],
            "run_plan_hash": self.plan["run_plan_hash"],
            "base_snapshot_hash": self.plan["snapshot_hash"],
            "files": files,
            "completed_operation_ids": sorted(self._completed),
        }
        report["result_hash"] = artifact_hash(report)
        return report
