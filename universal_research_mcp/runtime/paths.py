"""Resolve runtime paths without allowing a configured path to escape its project."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from universal_research_mcp.runtime.project_io import checked_project_path


def _inside(root: Path, value: str | Path) -> Path:
    supplied = Path(value)
    if supplied.is_absolute():
        raise ValueError(f"project path must be relative: {value}")
    candidate = (root / supplied).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"project path escapes root: {value}") from exc
    return candidate


@dataclass(frozen=True)
class ProjectPaths:
    """Canonical and derived locations bound to one resolved project root."""

    root: Path
    events_root: Path
    index_root: Path
    lexical_db: Path
    index_health: Path
    semantic_db: Path
    semantic_health: Path

    @classmethod
    def from_root(
        cls,
        root: str | Path,
        *,
        events_root: str | Path = "data/events",
        index_root: str | Path = "data/index",
        lexical_db: str | Path = "data/index/research.sqlite",
        index_health: str | Path = "data/index/index-health.json",
        semantic_db: str | Path = "data/index/semantic.sqlite",
        semantic_health: str | Path = "data/index/semantic-health.json",
    ) -> "ProjectPaths":
        resolved_root = Path(root).expanduser().resolve()
        return cls(
            root=resolved_root,
            events_root=_inside(resolved_root, events_root),
            index_root=_inside(resolved_root, index_root),
            lexical_db=_inside(resolved_root, lexical_db),
            index_health=_inside(resolved_root, index_health),
            semantic_db=_inside(resolved_root, semantic_db),
            semantic_health=_inside(resolved_root, semantic_health),
        )

    def resolve_relative(self, value: str | Path) -> Path:
        """Resolve a user/profile path and reject absolute and symlink escapes."""

        return _inside(self.root, value)

    @property
    def ingest_pending(self) -> Path:
        return checked_project_path(self.root, "data/ingest-drafts/pending", directory=True)

    @property
    def ingest_consumed(self) -> Path:
        return checked_project_path(self.root, "data/ingest-drafts/consumed", directory=True)

    @property
    def ingest_transactions(self) -> Path:
        return checked_project_path(self.root, "data/ingest-drafts/transactions", directory=True)

    @property
    def ingest_audit(self) -> Path:
        return checked_project_path(self.root, "data/audit", directory=True)

    @property
    def canonical_lock(self) -> Path:
        return checked_project_path(self.root, self.ingest_pending / ".commit.lock")
