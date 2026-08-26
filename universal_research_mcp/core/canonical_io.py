"""Shared canonical serialization and atomic, durable append operations.

Callers hold canonical_write_lock across validation and mutation. MCP wraps
these single-file primitives in its receipt-bound multi-file recovery journal;
the administrator CLI appends one file atomically under the same lock.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

from universal_research_mcp.runtime.paths import ProjectPaths
from universal_research_mcp.runtime.project_io import ProjectFiles


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@contextmanager
def canonical_write_lock(paths: ProjectPaths) -> Iterator[None]:
    """Fail closed when any CLI or MCP canonical writer already holds the lock."""

    files = ProjectFiles(paths.root)
    with files.parent(paths.canonical_lock, create=True) as (parent, name):
        payload = json.dumps({
            "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(),
        }).encode("utf-8") + b"\n"
        try:
            identity = parent.create(name, payload)
        except FileExistsError as exc:
            raise RuntimeError(
                "another ingest commit is active or an administrator holds the canonical write lock"
            ) from exc
        try:
            yield
        finally:
            parent.remove(name, identity=identity)


def prepare_append(
    paths: ProjectPaths, *, target: Path,
    append_value: dict[str, Any] | list[dict[str, Any]], kind: str,
) -> dict[str, Any]:
    files = ProjectFiles(paths.root)
    target = files.path(target)
    prior = files.read(target)
    before = prior if prior is not None else b""
    values = append_value if isinstance(append_value, list) else [append_value]
    append_bytes = "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values
    ).encode("utf-8")
    after = before + append_bytes
    return {
        "kind": kind,
        "target": target.relative_to(paths.root).as_posix(),
        "before_exists": prior is not None,
        "before_size": len(before),
        "before_sha256": _sha256(before),
        "append_text": append_bytes.decode("utf-8"),
        "append_sha256": _sha256(append_bytes),
        "after_size": len(after),
        "after_sha256": _sha256(after),
    }


def append_state(paths: ProjectPaths, operation: dict[str, Any]) -> str:
    target = operation.get("target")
    if not isinstance(target, str) or Path(target).is_absolute():
        raise ValueError("canonical transaction target is invalid")
    prior = ProjectFiles(paths.root).read(target)
    current = prior if prior is not None else b""
    state = (len(current), _sha256(current))
    if state == (operation.get("after_size"), operation.get("after_sha256")):
        return "applied"
    if (
        state == (operation.get("before_size"), operation.get("before_sha256"))
        and (prior is not None) is operation.get("before_exists")
    ):
        return "pending"
    raise RuntimeError(f"canonical target changed outside ingest transaction: {target}")


def apply_append(paths: ProjectPaths, operation: dict[str, Any]) -> str:
    state = append_state(paths, operation)
    if state == "applied":
        # A prior attempt may have been interrupted between rename and fsync.
        ProjectFiles(paths.root).sync(operation["target"])
        return state
    target = operation.get("target")
    append_text = operation.get("append_text")
    if not isinstance(target, str) or not isinstance(append_text, str):
        raise ValueError("canonical transaction operation is invalid")
    append_bytes = append_text.encode("utf-8")
    if _sha256(append_bytes) != operation.get("append_sha256"):
        raise ValueError("canonical transaction append payload hash is invalid")
    files = ProjectFiles(paths.root)
    before = files.read(target)
    after = (before if before is not None else b"") + append_bytes
    if (len(after), _sha256(after)) != (operation.get("after_size"), operation.get("after_sha256")):
        raise RuntimeError("canonical target changed before preparing atomic replacement")
    files.replace(target, after, expected=before, check_expected=True)
    if append_state(paths, operation) != "applied":
        raise RuntimeError("canonical transaction operation verification failed")
    return "applied"


__all__ = ["canonical_write_lock", "prepare_append", "append_state", "apply_append"]
