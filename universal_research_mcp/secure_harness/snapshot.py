"""Content-addressed, symlink-free project snapshots for workers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Any, Iterable

from universal_research_mcp.governance.hashing import artifact_hash

from .contracts import HarnessContractError


MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
MAX_SNAPSHOT_FILES = 100_000


def _relative(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(part in {".git", ".codex", ".agents"} for part in path.parts):
        raise HarnessContractError("snapshot path is outside the allowed project surface")
    return Path(*path.parts)


def _regular_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise HarnessContractError(f"snapshot input is not a single-link regular file: {path}")
    if info.st_size > MAX_FILE_BYTES:
        raise HarnessContractError(f"snapshot input exceeds the per-file limit: {path}")
    return info


def build_manifest(root: str | Path, paths: Iterable[str]) -> dict[str, Any]:
    project = Path(root).resolve(strict=True)
    entries: list[dict[str, Any]] = []
    total = 0
    seen: set[str] = set()
    for raw in sorted(set(paths)):
        relative = _relative(raw)
        candidate = project / relative
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(project)
        except FileNotFoundError:
            try:
                candidate.parent.resolve(strict=True).relative_to(project)
            except (OSError, ValueError) as exc:
                raise HarnessContractError(f"snapshot path parent is missing or escapes project: {raw}") from exc
            entries.append({"path": relative.as_posix(), "sha256": None, "size": 0, "mode": 0o600, "absent": True})
            continue
        except (OSError, ValueError) as exc:
            raise HarnessContractError(f"snapshot path escapes project: {raw}") from exc
        candidates = [resolved]
        if resolved.is_dir():
            candidates = sorted(item for item in resolved.rglob("*") if not item.is_dir())
        for item in candidates:
            info = _regular_file(item)
            rel = item.relative_to(project).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            total += info.st_size
            if len(seen) > MAX_SNAPSHOT_FILES or total > MAX_SNAPSHOT_BYTES:
                raise HarnessContractError("snapshot exceeds bounded file or byte limits")
            digest = hashlib.sha256(item.read_bytes()).hexdigest()
            entries.append({"path": rel, "sha256": digest, "size": info.st_size, "mode": stat.S_IMODE(info.st_mode)})
    manifest = {"schema_version": "worker-snapshot/1.0", "files": entries, "total_bytes": total}
    manifest["snapshot_hash"] = artifact_hash(manifest)
    return manifest


def materialize_snapshot(root: str | Path, manifest: dict[str, Any], destination: str | Path) -> Path:
    project = Path(root).resolve(strict=True)
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    expected = manifest.get("snapshot_hash")
    if expected != artifact_hash({key: value for key, value in manifest.items() if key != "snapshot_hash"}):
        raise HarnessContractError("snapshot manifest hash mismatch")
    for entry in manifest.get("files", []):
        relative = _relative(entry["path"])
        if entry.get("absent") is True:
            if (project / relative).exists():
                raise HarnessContractError("planned-absent snapshot path now exists")
            continue
        source = project / relative
        info = _regular_file(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != entry.get("sha256") or info.st_size != entry.get("size"):
            raise HarnessContractError("project changed after snapshot approval")
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, output, follow_symlinks=False)
        output.chmod(int(entry.get("mode", 0o600)) & 0o777)
    return target
