"""Offline integrity records for managed local model snapshots.

The initial baseline comes from an explicitly approved, commit-pinned download.
These records detect subsequent cache changes; they are not publisher signatures
or an OS sandbox against a process that can rewrite both config and model files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from universal_research_mcp.runtime.project_io import ProjectFiles


MANIFEST_NAME = ".urmcp-model-snapshot.json"
MANIFEST_SCHEMA_VERSION = "semantic-model-snapshot/1.0"
_HUB_METADATA = PurePosixPath(".cache/huggingface")
_MANIFEST_MAX_BYTES = 4 * 1024 * 1024


def immutable_revision(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
        raise ValueError(
            "semantic setup requires a full 40-character commit SHA as --revision; "
            "branches, tags and abbreviated hashes are not immutable revisions"
        )
    return value.lower()


def _model_id(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[\w.-]+/[\w.-]+", value, re.ASCII) is None:
        raise ValueError("model snapshot model_id is invalid")
    return value


def _sha256_value(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("model snapshot SHA-256 is invalid")
    return value


@dataclass(frozen=True)
class SnapshotIdentity:
    model_id: str
    revision: str
    manifest_sha256: str

    @classmethod
    def from_dict(cls, value: Any) -> SnapshotIdentity:
        if not isinstance(value, dict) or set(value) != {"model_id", "revision", "manifest_sha256"}:
            raise ValueError("model snapshot identity is invalid")
        return cls(
            _model_id(value["model_id"]), immutable_revision(value["revision"]),
            _sha256_value(value["manifest_sha256"]),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _ignored(relative: PurePosixPath) -> bool:
    return relative == PurePosixPath(MANIFEST_NAME) or relative.is_relative_to(_HUB_METADATA)


def _file_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _file_record(files: ProjectFiles, path: Path) -> dict[str, Any]:
    files.path(path)
    before = path.lstat()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        files.path(path)
        if _file_signature(before) != _file_signature(opened):
            raise ValueError("model snapshot file changed while opening")
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        after = os.fstat(handle.fileno())
    files.path(path)
    if _file_signature(before) != _file_signature(after) or _file_signature(before) != _file_signature(path.lstat()):
        raise ValueError("model snapshot file changed while hashing")
    return {"size_bytes": after.st_size, "sha256": digest}


def _inventory(snapshot: Path) -> dict[str, Any]:
    files = ProjectFiles(snapshot)
    files.path(snapshot, directory=True)
    if not snapshot.is_dir():
        raise ValueError("model snapshot directory is missing")
    records: dict[str, Any] = {}
    pending = [snapshot]
    while pending:
        directory = pending.pop()
        files.path(directory, directory=True)
        for path in sorted(directory.iterdir()):
            relative = PurePosixPath(path.relative_to(snapshot).as_posix())
            # Check even ignored bookkeeping roots before declining to descend.
            is_directory = path.is_dir() and not path.is_symlink()
            files.path(path, directory=is_directory)
            if _ignored(relative):
                continue
            if "\\" in str(relative) or ":" in str(relative):
                raise ValueError("model snapshot file name is not portable")
            if is_directory:
                pending.append(path)
            else:
                records[str(relative)] = _file_record(files, path)
    if not records:
        raise ValueError("model snapshot contains no model files")
    return records


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("model snapshot manifest contains duplicate keys")
        result[key] = value
    return result


def _read_manifest(snapshot: Path) -> tuple[SnapshotIdentity, dict[str, Any]]:
    payload = ProjectFiles(snapshot).read(MANIFEST_NAME, max_bytes=_MANIFEST_MAX_BYTES)
    if payload is None:
        raise ValueError("model snapshot manifest is missing; unverified caches cannot be reused")
    try:
        manifest = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("model snapshot manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "model_id", "revision", "files"}:
        raise ValueError("model snapshot manifest is incomplete")
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ValueError("model snapshot manifest schema is invalid")
    identity = SnapshotIdentity.from_dict({
        "model_id": manifest["model_id"], "revision": manifest["revision"],
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
    })
    records = manifest["files"]
    if not isinstance(records, dict) or not records:
        raise ValueError("model snapshot manifest has no file inventory")
    for name, record in records.items():
        relative = PurePosixPath(name)
        if (
            not name or relative.is_absolute() or ".." in relative.parts
            or str(relative) != name or "\\" in name or ":" in name or _ignored(relative)
        ):
            raise ValueError("model snapshot manifest file path is invalid")
        if not isinstance(record, dict) or set(record) != {"size_bytes", "sha256"}:
            raise ValueError("model snapshot manifest file record is invalid")
        size = record["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("model snapshot manifest file size is invalid")
        _sha256_value(record["sha256"])
    return identity, records


def read_snapshot_identity(snapshot: Path) -> SnapshotIdentity:
    """Read the small manifest only; full file verification is separate."""

    return _read_manifest(snapshot)[0]


def verify_snapshot(snapshot: Path, expected: SnapshotIdentity) -> None:
    actual, records = _read_manifest(snapshot)
    if actual != expected:
        raise ValueError("model snapshot identity or manifest hash does not match the approved configuration")
    if _inventory(snapshot) != records:
        raise ValueError("model snapshot files do not match the verified manifest")


def create_snapshot_manifest(snapshot: Path, *, model_id: str, revision: str) -> SnapshotIdentity:
    """Record only a newly completed download; never overwrite an old baseline."""

    model_id = _model_id(model_id)
    revision = immutable_revision(revision)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "model_id": model_id, "revision": revision,
        "files": _inventory(snapshot),
    }
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    if len(payload) > _MANIFEST_MAX_BYTES:
        raise ValueError("model snapshot manifest is too large")
    ProjectFiles(snapshot).create(MANIFEST_NAME, payload)
    return SnapshotIdentity(model_id, revision, hashlib.sha256(payload).hexdigest())


__all__ = [
    "MANIFEST_NAME", "SnapshotIdentity", "create_snapshot_manifest",
    "immutable_revision", "read_snapshot_identity", "verify_snapshot",
]
