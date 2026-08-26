"""Closed, hash-bound source contracts for sealed test operations."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Sequence

from universal_research_mcp.governance.hashing import artifact_hash

from .contracts import HarnessContractError


TEST_CONTRACT_VERSION = "harness-test-contract/1.0"
CHECK_KINDS = frozenset({
    "python_symbol",
    "python_literal",
    "python_assignment",
    "jsonl_key",
})
MAX_CONTRACTS = 128
MAX_CHECKS_PER_CONTRACT = 256
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_EXPECTED_BYTES = 16 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PYTHON_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_FORBIDDEN_PATH_PARTS = frozenset({".git", ".codex", ".agents"})


def _identifier(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not _IDENTIFIER.fullmatch(value)
        or ".." in value
    ):
        raise HarnessContractError(f"{label} is invalid")
    return value


def _python_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _PYTHON_IDENTIFIER.fullmatch(value):
        raise HarnessContractError(f"{label} is invalid")
    return value


def _relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise HarnessContractError(f"{label} must be a non-empty relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(part in _FORBIDDEN_PATH_PARTS for part in path.parts)
        or path.as_posix() in {"", "."}
    ):
        raise HarnessContractError(f"{label} escapes or enters a protected path")
    return path.as_posix()


def _json_value(value: object, label: str) -> Any:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HarnessContractError(f"{label} must be a finite JSON value") from exc
    if len(payload) > MAX_EXPECTED_BYTES:
        raise HarnessContractError(f"{label} exceeds the bounded JSON value limit")
    return json.loads(payload.decode("utf-8"))


def _normalize_check(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "check_id", "path", "kind", "selector", "expected",
    }:
        raise HarnessContractError("test contract check has an unsupported shape")
    kind = value.get("kind")
    if kind not in CHECK_KINDS:
        raise HarnessContractError("test contract check kind is unsupported")
    selector = value.get("selector")
    if not isinstance(selector, str) or not selector or len(selector.encode("utf-8")) > 4096:
        raise HarnessContractError("test contract check selector is invalid")
    expected = _json_value(value.get("expected"), "test contract expected value")
    if kind in {"python_symbol", "python_assignment"}:
        _python_identifier(selector, "test contract Python selector")
    if kind in {"python_symbol", "python_literal"} and expected is not True:
        raise HarnessContractError(f"{kind} checks require expected=true")
    return {
        "check_id": _identifier(value.get("check_id"), "test contract check_id"),
        "path": _relative(value.get("path"), "test contract check path"),
        "kind": kind,
        "selector": selector,
        "expected": expected,
    }


def _test_operations(operations: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for operation in operations:
        if not isinstance(operation, Mapping) or operation.get("kind") != "test":
            continue
        operation_id = _identifier(operation.get("operation_id"), "test operation_id")
        paths = operation.get("paths")
        if not isinstance(paths, list) or not paths:
            raise HarnessContractError("test operation paths must be a non-empty array")
        if operation_id in result:
            raise HarnessContractError("test operation IDs must be unique")
        result[operation_id] = [
            _relative(path, "test operation path") for path in paths
        ]
    return result


def _covered(path: str, allowed: Sequence[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate == PurePosixPath(item)
        or PurePosixPath(item) in candidate.parents
        for item in allowed
    )


def _source_bytes(root: Path, relative: str) -> bytes:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
        info = lexical.lstat()
    except (OSError, ValueError) as exc:
        raise HarnessContractError(f"test contract source is missing or escapes project: {relative}") from exc
    if (
        lexical != resolved
        or lexical.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        raise HarnessContractError(f"test contract source is not a symlink-free regular file: {relative}")
    if info.st_size > MAX_SOURCE_BYTES:
        raise HarnessContractError(f"test contract source exceeds four MiB: {relative}")
    return lexical.read_bytes()


def _python_tree(payload: bytes, path: str) -> ast.Module:
    try:
        return ast.parse(payload.decode("utf-8"), filename=path)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise HarnessContractError(f"test contract Python source is invalid: {path}") from exc


def _python_symbols(tree: ast.Module) -> set[str]:
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            symbols.update(target.id for target in targets if isinstance(target, ast.Name))
    return symbols


def _assignment_value(tree: ast.Module, selector: str) -> Any:
    for node in tree.body:
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == selector for target in node.targets
        ):
            value_node = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == selector
        ):
            value_node = node.value
        if value_node is not None:
            try:
                raw = ast.literal_eval(value_node)
            except (ValueError, TypeError) as exc:
                raise HarnessContractError(
                    f"test contract Python assignment is not literal: {selector}"
                ) from exc
            if isinstance(raw, tuple):
                raw = list(raw)
            return _json_value(raw, "test contract Python assignment")
    raise HarnessContractError(f"test contract Python assignment does not exist: {selector}")


def _evaluate_check(check: Mapping[str, Any], payload: bytes) -> None:
    kind = check["kind"]
    selector = check["selector"]
    path = check["path"]
    if kind.startswith("python_"):
        tree = _python_tree(payload, path)
        if kind == "python_symbol" and selector not in _python_symbols(tree):
            raise HarnessContractError(f"test contract Python symbol does not exist: {selector}")
        if kind == "python_literal" and not any(
            isinstance(node, ast.Constant) and node.value == selector for node in ast.walk(tree)
        ):
            raise HarnessContractError(f"test contract Python literal does not exist: {selector}")
        if kind == "python_assignment" and _assignment_value(tree, selector) != check["expected"]:
            raise HarnessContractError(f"test contract Python assignment changed: {selector}")
        return
    rows: list[dict[str, Any]] = []
    try:
        for line in payload.decode("utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError
                rows.append(item)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HarnessContractError(f"test contract JSONL source is invalid: {path}") from exc
    if not rows or any(row.get(selector) != check["expected"] for row in rows):
        raise HarnessContractError(f"test contract JSONL key/value changed: {selector}")


def _contract_operation_ids(
    contracts: Sequence[Mapping[str, Any]],
    operations: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    test_operations = _test_operations(operations)
    contract_operation_ids = [str(item["operation_id"]) for item in contracts]
    if (
        len(contract_operation_ids) != len(set(contract_operation_ids))
        or set(contract_operation_ids) != set(test_operations)
    ):
        raise HarnessContractError("each test operation requires exactly one test contract")
    return test_operations


def seal_test_contracts(
    root: str | Path,
    value: object,
    operations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate source assumptions and seal them into deterministic plan material."""

    if not isinstance(value, list) or len(value) > MAX_CONTRACTS:
        raise HarnessContractError("test_contracts must be a bounded array")
    project = Path(root).resolve(strict=True)
    raw_contracts: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "schema_version", "contract_id", "operation_id", "checks",
        }:
            raise HarnessContractError("test contract has an unsupported shape")
        if item.get("schema_version") != TEST_CONTRACT_VERSION:
            raise HarnessContractError("test contract schema is unsupported")
        raw_contracts.append(item)
    test_operations = _contract_operation_ids(raw_contracts, operations)
    sealed: list[dict[str, Any]] = []
    contract_ids: set[str] = set()
    for item in raw_contracts:
        contract_id = _identifier(item.get("contract_id"), "test contract_id")
        if contract_id in contract_ids:
            raise HarnessContractError("test contract IDs must be unique")
        contract_ids.add(contract_id)
        operation_id = _identifier(item.get("operation_id"), "test contract operation_id")
        checks_value = item.get("checks")
        if (
            not isinstance(checks_value, list)
            or not checks_value
            or len(checks_value) > MAX_CHECKS_PER_CONTRACT
        ):
            raise HarnessContractError("test contract checks must be a bounded non-empty array")
        checks = sorted((_normalize_check(check) for check in checks_value), key=lambda row: row["check_id"])
        check_ids = [check["check_id"] for check in checks]
        if len(check_ids) != len(set(check_ids)):
            raise HarnessContractError("test contract check IDs must be unique")
        allowed = test_operations[operation_id]
        if any(not _covered(check["path"], allowed) for check in checks):
            raise HarnessContractError("test contract source is outside its test operation paths")
        payloads = {path: _source_bytes(project, path) for path in sorted({check["path"] for check in checks})}
        for check in checks:
            _evaluate_check(check, payloads[check["path"]])
        sources = [
            {
                "path": path,
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
            for path, payload in payloads.items()
        ]
        contract = {
            "schema_version": TEST_CONTRACT_VERSION,
            "contract_id": contract_id,
            "operation_id": operation_id,
            "checks": checks,
            "sources": sources,
        }
        contract["contract_hash"] = artifact_hash(contract)
        sealed.append(contract)
    return sorted(sealed, key=lambda row: row["contract_id"])


def validate_test_contracts(
    value: object,
    operations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate already-sealed test contracts without reading the filesystem."""

    if not isinstance(value, list) or len(value) > MAX_CONTRACTS:
        raise HarnessContractError("test_contracts must be a bounded array")
    normalized: list[dict[str, Any]] = []
    contract_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "schema_version", "contract_id", "operation_id", "checks", "sources", "contract_hash",
        }:
            raise HarnessContractError("sealed test contract has an unsupported shape")
        if item.get("schema_version") != TEST_CONTRACT_VERSION:
            raise HarnessContractError("test contract schema is unsupported")
        contract_id = _identifier(item.get("contract_id"), "test contract_id")
        operation_id = _identifier(item.get("operation_id"), "test contract operation_id")
        checks_value = item.get("checks")
        sources_value = item.get("sources")
        if (
            not isinstance(checks_value, list)
            or not checks_value
            or len(checks_value) > MAX_CHECKS_PER_CONTRACT
            or not isinstance(sources_value, list)
            or not sources_value
        ):
            raise HarnessContractError("sealed test contract is empty or unbounded")
        checks = sorted((_normalize_check(check) for check in checks_value), key=lambda row: row["check_id"])
        check_ids = [check["check_id"] for check in checks]
        if len(check_ids) != len(set(check_ids)):
            raise HarnessContractError("test contract check IDs must be unique")
        sources: list[dict[str, Any]] = []
        for source in sources_value:
            if not isinstance(source, Mapping) or set(source) != {"path", "sha256", "size"}:
                raise HarnessContractError("test contract source binding has an unsupported shape")
            path = _relative(source.get("path"), "test contract source path")
            digest = source.get("sha256")
            size = source.get("size")
            if (
                not isinstance(digest, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or size > MAX_SOURCE_BYTES
            ):
                raise HarnessContractError("test contract source binding is invalid")
            sources.append({"path": path, "sha256": digest, "size": size})
        sources.sort(key=lambda row: row["path"])
        if len({source["path"] for source in sources}) != len(sources):
            raise HarnessContractError("test contract source paths must be unique")
        if {check["path"] for check in checks} != {source["path"] for source in sources}:
            raise HarnessContractError("test contract checks and source bindings differ")
        contract: dict[str, Any] = {
            "schema_version": TEST_CONTRACT_VERSION,
            "contract_id": contract_id,
            "operation_id": operation_id,
            "checks": checks,
            "sources": sources,
        }
        if item.get("contract_hash") != artifact_hash(contract):
            raise HarnessContractError("test contract hash mismatch")
        contract["contract_hash"] = str(item["contract_hash"])
        if contract_id in contract_ids:
            raise HarnessContractError("test contract IDs must be unique")
        contract_ids.add(contract_id)
        normalized.append(contract)
    normalized.sort(key=lambda row: row["contract_id"])
    test_operations = _contract_operation_ids(normalized, operations)
    for contract in normalized:
        allowed = test_operations[contract["operation_id"]]
        if any(not _covered(check["path"], allowed) for check in contract["checks"]):
            raise HarnessContractError("test contract source is outside its test operation paths")
    return normalized


def verify_test_contracts(
    root: str | Path,
    value: object,
    operations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Recheck sealed assumptions and source hashes at the execution boundary."""

    project = Path(root).resolve(strict=True)
    contracts = validate_test_contracts(value, operations)
    for contract in contracts:
        payloads: dict[str, bytes] = {}
        for source in contract["sources"]:
            payload = _source_bytes(project, source["path"])
            if (
                len(payload) != source["size"]
                or "sha256:" + hashlib.sha256(payload).hexdigest() != source["sha256"]
            ):
                raise HarnessContractError(
                    f"test contract source hash mismatch: {source['path']}"
                )
            payloads[source["path"]] = payload
        for check in contract["checks"]:
            _evaluate_check(check, payloads[check["path"]])
    return contracts
