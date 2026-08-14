"""Content-bound publication manifest for an unauthenticated demo MCP.

The public demo transport is deliberately narrower than the local stdio MCP.
An operator must explicitly enumerate and hash the canonical ledger, every
registered source, and every derived/configuration file used by retrieval.
Serving fails closed when the projection changes after that review.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
from typing import Any, Iterable


SCHEMA_VERSION = "public-demo-manifest/1.0"
DEFAULT_MANIFEST_PATH = Path("config/public-demo.json")
PUBLIC_CONFIRMATION = "I_UNDERSTAND_THIS_DATA_WILL_BE_PUBLIC"
_CORPUS_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}\Z")
_DERIVED_CANDIDATES = (
    Path("data/index/research.sqlite"),
    Path("data/index/index-health.json"),
    Path("data/index/semantic.sqlite"),
    Path("data/index/semantic-health.json"),
    Path("config/semantic.json"),
    Path("config/research-profile.json"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _project_path(root: Path, relative: str | Path, *, must_exist: bool = True) -> Path:
    raw = PurePosixPath(str(relative).replace("\\", "/"))
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise ValueError(f"public demo path is not project-relative: {relative}")
    candidate = root.joinpath(*raw.parts)
    current = root
    for part in raw.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"public demo path must not traverse a symlink: {relative}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"public demo path escapes the project: {relative}") from exc
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(f"public demo file is missing: {relative}")
    return resolved


def manifest_path(root: str | Path, relative: str | Path = DEFAULT_MANIFEST_PATH) -> Path:
    resolved_root = Path(root).expanduser().resolve()
    return _project_path(resolved_root, relative, must_exist=False)


def _file_entry(root: Path, path: Path) -> dict[str, Any]:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"public demo file is outside the project: {path}") from exc
    checked = _project_path(root, relative)
    return {
        "path": relative,
        "sha256": _sha256(checked),
        "size_bytes": checked.stat().st_size,
    }


def _canonical_files(root: Path) -> list[Path]:
    events_root = root / "data/events"
    if (root / "data").is_symlink() or events_root.is_symlink() or not events_root.is_dir():
        raise ValueError("canonical event directory is missing or is a symlink")
    files = sorted(path for path in events_root.rglob("*") if path.is_file())
    if not files:
        raise ValueError("public demo corpus must contain canonical records")
    if any(path.is_symlink() for path in files):
        raise ValueError("canonical event files must not contain symlinks")
    return files


def _open_lexical(root: Path) -> sqlite3.Connection:
    database = _project_path(root, "data/index/research.sqlite")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _registered_source_paths(root: Path) -> list[str]:
    with _open_lexical(root) as connection:
        rows = connection.execute(
            """
            SELECT source_path FROM events
            WHERE source_path IS NOT NULL AND source_path <> ''
            UNION
            SELECT source_path FROM sources
            WHERE source_path IS NOT NULL AND source_path <> ''
            """
        ).fetchall()
    return sorted({str(row["source_path"]) for row in rows})


def _event_count(root: Path) -> int:
    with _open_lexical(root) as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()
    return int(row["count"] if row is not None else 0)


def _derived_files(root: Path) -> list[Path]:
    return [root / relative for relative in _DERIVED_CANDIDATES if (root / relative).is_file()]


def _entries(root: Path, paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [_file_entry(root, path) for path in paths]


def build_manifest(
    root: str | Path,
    *,
    corpus_id: str,
    display_name: str,
    confirmation: str,
) -> dict[str, Any]:
    """Build an in-memory publication manifest after an explicit disclosure ack."""

    unresolved_root = Path(root).expanduser()
    if unresolved_root.is_symlink():
        raise ValueError("public demo root must not be a symlink")
    resolved_root = unresolved_root.resolve()
    if not resolved_root.is_dir():
        raise FileNotFoundError("public demo root does not exist")
    if confirmation != PUBLIC_CONFIRMATION:
        raise ValueError(f"confirmation must exactly equal {PUBLIC_CONFIRMATION}")
    if not _CORPUS_ID.fullmatch(corpus_id):
        raise ValueError("corpus_id must be 3-64 lowercase URL-safe characters")
    if not display_name.strip() or len(display_name.strip()) > 120:
        raise ValueError("display_name must contain 1-120 characters")

    from universal_research_mcp.indexing import index_status
    from universal_research_mcp.runtime.semantic_config import load_semantic_config

    status = index_status(resolved_root)
    if status.get("status") != "current" or status.get("integrity") != "ok":
        raise RuntimeError("lexical index must be current and integrity-checked before publication")
    semantic = load_semantic_config(resolved_root)
    if semantic is not None:
        backend = semantic["backend"]
        if backend["kind"] == "local_sentence_transformer":
            if backend.get("trust_local_model_code") is True:
                raise ValueError("public demo mode rejects trust_local_model_code=true")
            raise ValueError(
                "public demo mode currently requires lexical or deterministic signed-hashing "
                "retrieval; a local model needs a separately pinned model-snapshot manifest"
            )

    event_count = _event_count(resolved_root)
    if event_count < 1:
        raise ValueError("public demo corpus must contain at least one indexed event")
    source_paths = _registered_source_paths(resolved_root)
    sources = [_project_path(resolved_root, path) for path in source_paths]
    canonical = _canonical_files(resolved_root)
    derived = _derived_files(resolved_root)
    if not any(path.name == "research.sqlite" for path in derived):
        raise ValueError("public demo lexical database is missing")

    groups = {
        "canonical": _entries(resolved_root, canonical),
        "sources": _entries(resolved_root, sources),
        "derived_and_config": _entries(resolved_root, derived),
    }
    all_paths = [entry["path"] for entries in groups.values() for entry in entries]
    if len(all_paths) != len(set(all_paths)):
        raise ValueError("public demo manifest file groups overlap")
    return {
        "schema_version": SCHEMA_VERSION,
        "corpus_id": corpus_id,
        "display_name": display_name.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "operator_confirmed_public_data": True,
        "canonical_write_disabled": True,
        "event_count": event_count,
        "files": groups,
    }


def write_manifest(
    root: str | Path,
    manifest: dict[str, Any],
    *,
    relative_path: str | Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    """Atomically persist one reviewed publication manifest inside the project."""

    resolved_root = Path(root).expanduser().resolve()
    destination = manifest_path(resolved_root, relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".staging", dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "prepared",
        "manifest_path": destination.relative_to(resolved_root).as_posix(),
        "manifest_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "corpus_id": manifest["corpus_id"],
        "event_count": manifest["event_count"],
        "canonical_write_disabled": True,
        "server_started": False,
    }


def _validate_entry(root: Path, entry: Any) -> str:
    if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
        raise ValueError("public demo file entry is invalid")
    relative = entry.get("path")
    digest = entry.get("sha256")
    size = entry.get("size_bytes")
    if not isinstance(relative, str) or not isinstance(digest, str) or not isinstance(size, int):
        raise ValueError("public demo file entry types are invalid")
    path = _project_path(root, relative)
    if path.stat().st_size != size or _sha256(path) != digest:
        raise RuntimeError(f"public demo file changed after review: {relative}")
    return relative


def validate_manifest(
    root: str | Path,
    *,
    relative_path: str | Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    """Recompute the exact public projection and reject any unreviewed change."""

    unresolved_root = Path(root).expanduser()
    if unresolved_root.is_symlink():
        raise ValueError("public demo root must not be a symlink")
    resolved_root = unresolved_root.resolve()
    path = manifest_path(resolved_root, relative_path)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("public demo manifest is missing")
    payload = path.read_bytes()
    document = json.loads(payload)
    expected_keys = {
        "schema_version", "corpus_id", "display_name", "created_at",
        "operator_confirmed_public_data", "canonical_write_disabled", "event_count", "files",
    }
    if not isinstance(document, dict) or set(document) != expected_keys:
        raise ValueError("public demo manifest shape is invalid")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("public demo manifest schema_version is invalid")
    if document.get("operator_confirmed_public_data") is not True:
        raise ValueError("public demo data disclosure was not confirmed")
    if document.get("canonical_write_disabled") is not True:
        raise ValueError("public demo canonical-write boundary is invalid")
    if not isinstance(document.get("corpus_id"), str) or not _CORPUS_ID.fullmatch(document["corpus_id"]):
        raise ValueError("public demo corpus_id is invalid")
    files = document.get("files")
    if not isinstance(files, dict) or set(files) != {"canonical", "sources", "derived_and_config"}:
        raise ValueError("public demo file groups are invalid")
    validated: dict[str, list[str]] = {}
    for group, entries in files.items():
        if not isinstance(entries, list):
            raise ValueError(f"public demo {group} entries must be a list")
        validated[group] = [_validate_entry(resolved_root, entry) for entry in entries]
    flattened = [item for entries in validated.values() for item in entries]
    if len(flattened) != len(set(flattened)):
        raise ValueError("public demo manifest contains duplicate paths")

    actual_canonical = [path.relative_to(resolved_root).as_posix() for path in _canonical_files(resolved_root)]
    actual_sources = _registered_source_paths(resolved_root)
    actual_derived = [path.relative_to(resolved_root).as_posix() for path in _derived_files(resolved_root)]
    if validated["canonical"] != actual_canonical:
        raise RuntimeError("canonical event file set changed after public review")
    if validated["sources"] != actual_sources:
        raise RuntimeError("registered source set changed after public review")
    if validated["derived_and_config"] != actual_derived:
        raise RuntimeError("derived/configuration file set changed after public review")
    if _event_count(resolved_root) != document.get("event_count"):
        raise RuntimeError("indexed event count changed after public review")

    from universal_research_mcp.indexing import index_status

    index = index_status(resolved_root)
    if index.get("status") != "current" or index.get("integrity") != "ok":
        raise RuntimeError("public demo lexical index is not current and verified")
    return {
        "enabled": True,
        "status": "verified",
        "corpus_id": document["corpus_id"],
        "display_name": document["display_name"],
        "event_count": document["event_count"],
        "manifest_path": path.relative_to(resolved_root).as_posix(),
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        "canonical_file_count": len(validated["canonical"]),
        "source_file_count": len(validated["sources"]),
        "derived_file_count": len(validated["derived_and_config"]),
        "canonical_write_disabled": True,
    }


__all__ = [
    "DEFAULT_MANIFEST_PATH", "PUBLIC_CONFIRMATION", "SCHEMA_VERSION",
    "build_manifest", "manifest_path", "validate_manifest", "write_manifest",
]
