"""Bounded, authority-preserving evidence hydration for agent prompts.

The runtime never treats a search hit or an arbitrary project file as evidence.
It resolves a requested canonical record, verifies its current append-only view,
and opens only its exact registered source revision without following symlinks.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol

from universal_research_mcp.core.amendments import resolve_core_amendments
from universal_research_mcp.core.ledger import validate_records
from universal_research_mcp.governance.hashing import artifact_hash
from universal_research_mcp.governance.registry import load_registry
from universal_research_mcp.runtime import ProjectPaths


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SNAPSHOT_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DENIED_FRAGMENTS = frozenset(
    {"secret", "token", "credential", "private_key", "api_key", "apikey"}
)
_DENIED_BASENAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
        "credentials.json",
    }
)
_ELIGIBLE_CORE_STATUSES = frozenset({"approved", "active", "completed"})
_TERMINAL_IGNORED_AMENDMENT_STATUSES = frozenset({"rejected", "stopped", "superseded"})
_PENDING_AMENDMENT_STATUSES = frozenset({"draft", "proposed", "approved", "active"})
_VERIFIED_SOURCE_STATUSES = frozenset({"integrity_verified", "human_verified"})
_WITHDRAWN_MARKERS = frozenset(
    {"withdrawn", "retracted", "superseded", "refuted", "unsupported"}
)


@dataclass(frozen=True)
class EvidencePassage:
    record_id: str
    source_id: str
    path: str
    source_sha256: str
    line_start: int
    line_end: int
    content: str

    @property
    def evidence_ref(self) -> str:
        return (
            f"source:{self.source_id}|path:{self.path}|sha256:{self.source_sha256}"
            f"|lines:{self.line_start}-{self.line_end}"
        )

    def reference_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source_id": self.source_id,
            "path": self.path,
            "source_sha256": self.source_sha256,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "evidence_ref": self.evidence_ref,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.reference_dict(), "content": self.content}


@dataclass(frozen=True)
class EvidenceBundle:
    snapshot_hash: str
    boundary_hash: str
    passages: tuple[EvidencePassage, ...]
    authority_records: tuple[dict[str, Any], ...] = ()

    def material(self) -> dict[str, Any]:
        return {
            "schema_version": "agent-evidence-bundle/1.0",
            "snapshot_hash": self.snapshot_hash,
            "boundary_hash": self.boundary_hash,
            "passages": [passage.to_dict() for passage in self.passages],
            "authority_records": list(self.authority_records),
        }

    @property
    def bundle_hash(self) -> str:
        return artifact_hash(self.material())

    def to_dict(self) -> dict[str, Any]:
        return {**self.material(), "bundle_hash": self.bundle_hash}

    def approval_summary(self) -> dict[str, Any]:
        """Describe the exact outbound evidence without exposing its content."""

        passages = [passage.reference_dict() for passage in self.passages]
        return {
            "snapshot_hash": self.snapshot_hash,
            "boundary_hash": self.boundary_hash,
            "bundle_hash": self.bundle_hash,
            "passage_count": len(passages),
            "content_bytes": sum(
                len(passage.content.encode("utf-8")) for passage in self.passages
            ),
            "passages": passages,
            "authority_records": list(self.authority_records),
        }

    def contains_reference(self, reference: Any) -> bool:
        strings = {passage.evidence_ref for passage in self.passages}
        dictionaries = [passage.reference_dict() for passage in self.passages]
        if isinstance(reference, str):
            return reference in strings
        if isinstance(reference, dict):
            return reference in dictionaries
        return False


def evidence_snapshot_hash(
    boundary_hash: str,
    passages: tuple[EvidencePassage, ...],
    authority_records: tuple[dict[str, Any], ...] = (),
) -> str:
    """Hash content plus the canonical authority used to hydrate it."""

    return artifact_hash(
        {
            "schema_version": "agent-evidence-snapshot/1.0",
            "boundary_hash": boundary_hash,
            "passages": [passage.to_dict() for passage in passages],
            "authority_records": list(authority_records),
        }
    )


class EvidenceBundleBuilder(Protocol):
    def build(self, packet: dict[str, Any], root: Path) -> EvidenceBundle: ...


@dataclass(frozen=True)
class _SourceRegistration:
    source_id: str
    path: str
    source_sha256: str
    registration_hash: str
    registration_identity_hash: str
    manifest_line: int


@dataclass(frozen=True)
class _RecordView:
    record_id: str
    family: str
    canonical: dict[str, Any]
    resolved: dict[str, Any]
    canonical_hash: str
    resolved_hash: str
    applied_amendments: tuple[dict[str, Any], ...]


class ProjectEvidenceBundleBuilder:
    """Hydrate only current, registered, exact source ranges from a ledger."""

    def __init__(
        self,
        *,
        max_passages: int = 32,
        max_lines_per_passage: int = 400,
        max_total_bytes: int = 256 * 1024,
        max_source_bytes: int = 16 * 1024 * 1024,
        max_ledger_bytes: int = 64 * 1024 * 1024,
        max_records: int = 100_000,
        max_ledger_files: int = 4_096,
    ) -> None:
        if (
            min(
                max_passages,
                max_lines_per_passage,
                max_total_bytes,
                max_source_bytes,
                max_ledger_bytes,
                max_records,
                max_ledger_files,
            )
            < 1
        ):
            raise ValueError("evidence limits must be positive")
        self.max_passages = max_passages
        self.max_lines_per_passage = max_lines_per_passage
        self.max_total_bytes = max_total_bytes
        self.max_source_bytes = max_source_bytes
        self.max_ledger_bytes = max_ledger_bytes
        self.max_records = max_records
        self.max_ledger_files = max_ledger_files

    def build(self, packet: dict[str, Any], root: Path) -> EvidenceBundle:
        """Hydrate and require the task packet's declared snapshot to match."""

        return self._build(packet, root, require_declared_snapshot=True)

    def preview(self, packet: dict[str, Any], root: Path) -> EvidenceBundle:
        """Compute the snapshot that a task author must seal before execution.

        Preview performs the same authority, scope, and file checks as ``build``.
        It grants no execution authority and only omits the final equality check
        against the packet's existing ``snapshot_hash`` value.
        """

        return self._build(packet, root, require_declared_snapshot=False)

    def _build(
        self,
        packet: dict[str, Any],
        root: Path,
        *,
        require_declared_snapshot: bool,
    ) -> EvidenceBundle:
        self._reject_root_symlink_components(Path(root))
        paths = ProjectPaths.from_root(root)
        boundary = self._validated_boundary(packet)
        requested = [
            str(value)
            for field in ("record_ids", "result_ids")
            for value in (boundary.get(field) or [])
        ]
        if len(requested) != len(set(requested)):
            raise ValueError(
                "evidence_boundary contains duplicate requested record IDs"
            )

        boundary_material = {
            key: value for key, value in boundary.items() if key != "snapshot_hash"
        }
        boundary_hash = artifact_hash(boundary_material)
        if not requested:
            empty_passages: tuple[EvidencePassage, ...] = ()
            empty_authority_records: tuple[dict[str, Any], ...] = ()
            self._require_passages_for_role(packet, empty_passages)
            return self._finalize_bundle(
                boundary["snapshot_hash"],
                boundary_hash,
                empty_passages,
                empty_authority_records,
                require_declared_snapshot=require_declared_snapshot,
            )

        registered = self._registered_sources(paths)
        records = self._canonical_records(paths)
        views = self._record_views(records, requested)
        missing = sorted(set(requested) - set(views))
        if missing:
            raise ValueError("evidence records are missing: " + ", ".join(missing))

        collected_passages: list[EvidencePassage] = []
        collected_authority_records: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int, int]] = set()
        total_bytes = 0
        source_cache: dict[str, tuple[str, list[str], str]] = {}
        scope = packet.get("scope") or {}
        if not isinstance(scope, dict):
            raise ValueError("scope must be an object")
        allowed_paths = tuple(
            str(value) for value in (scope.get("allowed_paths") or [])
        )
        allowed_sources = tuple(
            str(value) for value in (scope.get("allowed_sources") or [])
        )

        for record_id in requested:
            view = views[record_id]
            references = self._references(view)
            if not references:
                raise ValueError(
                    f"evidence record has no exact source range: {record_id}"
                )
            for reference in references:
                key = (
                    record_id,
                    reference["path"],
                    reference["line_start"],
                    reference["line_end"],
                )
                if key in seen:
                    continue
                seen.add(key)
                if len(collected_passages) >= self.max_passages:
                    raise ValueError("evidence passage limit exceeded")
                line_count = reference["line_end"] - reference["line_start"] + 1
                if line_count > self.max_lines_per_passage:
                    raise ValueError(f"evidence line limit exceeded: {record_id}")

                expected = reference["source_sha256"].lower()
                registrations = registered.get((reference["path"], expected), ())
                if not registrations:
                    raise ValueError(
                        "evidence source is not registered with the exact hash: "
                        + reference["path"]
                    )
                if len(registrations) != 1:
                    raise ValueError(
                        f"evidence source registration is ambiguous: {reference['path']}"
                    )
                registration = registrations[0]
                if not self._scope_path_allowed(reference["path"], allowed_paths):
                    raise ValueError(
                        f"evidence source is outside scope.allowed_paths: {reference['path']}"
                    )
                if (
                    "canonical" not in allowed_sources
                    and not self._scope_source_allowed(
                        registration.source_id, reference["path"], allowed_sources
                    )
                ):
                    raise ValueError(
                        "evidence source is outside scope.allowed_sources: "
                        + registration.source_id
                    )

                self._validate_source_path(reference["path"])
                if reference["path"] not in source_cache:
                    source_bytes, source_identity_hash = self._read_project_file(
                        paths.root,
                        reference["path"],
                        max_bytes=self.max_source_bytes,
                        label="evidence source",
                    )
                    actual = hashlib.sha256(source_bytes).hexdigest()
                    try:
                        lines = source_bytes.decode("utf-8").splitlines()
                    except UnicodeDecodeError as exc:
                        raise ValueError(
                            f"evidence source is not valid UTF-8: {reference['path']}"
                        ) from exc
                    source_cache[reference["path"]] = (
                        actual,
                        lines,
                        source_identity_hash,
                    )
                actual, lines, source_identity_hash = source_cache[reference["path"]]
                if actual != expected:
                    raise ValueError(
                        f"evidence source hash mismatch: {reference['path']}"
                    )
                if reference["line_end"] > len(lines):
                    raise ValueError(
                        f"evidence range exceeds source: {reference['path']}"
                    )
                content = "\n".join(
                    lines[reference["line_start"] - 1 : reference["line_end"]]
                )
                total_bytes += len(content.encode("utf-8"))
                if total_bytes > self.max_total_bytes:
                    raise ValueError("evidence byte limit exceeded")

                passage = EvidencePassage(
                    record_id=record_id,
                    source_id=registration.source_id,
                    path=reference["path"],
                    source_sha256=actual,
                    line_start=reference["line_start"],
                    line_end=reference["line_end"],
                    content=content,
                )
                collected_passages.append(passage)
                collected_authority_records.append(
                    self._authority_material(
                        view,
                        reference,
                        registration,
                        source_identity_hash,
                        passage.evidence_ref,
                    )
                )

        materialized = tuple(collected_passages)
        authority = tuple(collected_authority_records)
        self._require_passages_for_role(packet, materialized)
        return self._finalize_bundle(
            boundary["snapshot_hash"],
            boundary_hash,
            materialized,
            authority,
            require_declared_snapshot=require_declared_snapshot,
        )

    @staticmethod
    def _finalize_bundle(
        declared_snapshot: str,
        boundary_hash: str,
        passages: tuple[EvidencePassage, ...],
        authority_records: tuple[dict[str, Any], ...],
        *,
        require_declared_snapshot: bool,
    ) -> EvidenceBundle:
        computed = evidence_snapshot_hash(boundary_hash, passages, authority_records)
        if require_declared_snapshot and declared_snapshot != computed:
            raise ValueError(
                "declared evidence snapshot does not match the hydrated bundle"
            )
        return EvidenceBundle(computed, boundary_hash, passages, authority_records)

    @staticmethod
    def _validated_boundary(packet: dict[str, Any]) -> dict[str, Any]:
        boundary = packet.get("evidence_boundary") or {}
        if not isinstance(boundary, dict):
            raise ValueError("evidence_boundary must be an object")
        declared_snapshot = boundary.get("snapshot_hash")
        if not isinstance(declared_snapshot, str) or not _SNAPSHOT_SHA256.fullmatch(
            declared_snapshot
        ):
            raise ValueError(
                "evidence snapshot_hash must be one exact sha256 artifact hash"
            )
        for field in (
            "record_ids",
            "result_ids",
            "dataset_hashes",
            "model_hashes",
            "artifact_revisions",
            "commit_ids",
        ):
            value = boundary.get(field) or []
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item for item in value
            ):
                raise ValueError(f"evidence_boundary.{field} must be a string array")
        unsupported = [
            field
            for field in (
                "dataset_hashes",
                "model_hashes",
                "artifact_revisions",
                "commit_ids",
            )
            if boundary.get(field)
        ]
        if unsupported:
            raise ValueError(
                "unsupported evidence boundary fields are non-empty: "
                + ", ".join(unsupported)
            )
        return boundary

    @staticmethod
    def _require_passages_for_role(
        packet: dict[str, Any], passages: tuple[EvidencePassage, ...]
    ) -> None:
        manifest = load_registry().get(str(packet.get("agent_id") or "")) or {}
        if manifest.get("evidence", {}).get("requires_source_fetch") and not passages:
            raise ValueError("role requires at least one hydrated source passage")

    def _registered_sources(
        self, paths: ProjectPaths
    ) -> dict[tuple[str, str], tuple[_SourceRegistration, ...]]:
        relative = paths.events_root.relative_to(paths.root) / "sources.jsonl"
        raw, _identity = self._read_project_file(
            paths.root,
            relative.as_posix(),
            max_bytes=self.max_source_bytes,
            label="canonical source manifest",
        )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("canonical source manifest is not valid UTF-8") from exc

        registered: dict[tuple[str, str], list[_SourceRegistration]] = {}
        source_id_bindings: dict[str, tuple[str, str]] = {}
        for number, line in enumerate(text.splitlines(), start=1):
            if number > self.max_records:
                raise ValueError("canonical source registration limit exceeded")
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid source manifest JSON at line {number}"
                ) from exc
            source_path = value.get("source_path") if isinstance(value, dict) else None
            digest = value.get("source_sha256") if isinstance(value, dict) else None
            source_id = value.get("source_id") if isinstance(value, dict) else None
            if (
                not isinstance(source_id, str)
                or not source_id
                or not isinstance(source_path, str)
                or not source_path
                or not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)
            ):
                raise ValueError(f"invalid source manifest entry at line {number}")
            normalized_path = self._normalize_relative_path(source_path)
            key = (normalized_path, digest.lower())
            prior = source_id_bindings.get(source_id)
            if prior is not None and prior != key:
                raise ValueError(
                    f"source_id is bound to multiple path/hash revisions: {source_id}"
                )
            source_id_bindings[source_id] = key
            registration_hash = artifact_hash(value)
            registration = _SourceRegistration(
                source_id=source_id,
                path=normalized_path,
                source_sha256=digest.lower(),
                registration_hash=registration_hash,
                registration_identity_hash=artifact_hash(
                    {
                        "source_id": source_id,
                        "logical_path": normalized_path,
                        "source_sha256": digest.lower(),
                        "manifest_line": number,
                        "registration_hash": registration_hash,
                    }
                ),
                manifest_line=number,
            )
            registered.setdefault(key, []).append(registration)
        return {key: tuple(value) for key, value in registered.items()}

    def _canonical_records(self, paths: ProjectPaths) -> list[dict[str, Any]]:
        daily = paths.events_root / "daily"
        if not daily.exists():
            return []
        daily_relative = daily.relative_to(paths.root).as_posix()
        self._open_project_directory(paths.root, daily_relative)

        candidates: list[str] = []
        with os.scandir(daily) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise ValueError(
                        "canonical event ledger directory contains a symlink"
                    )
                if not entry.is_dir(follow_symlinks=False):
                    continue
                relative = f"{daily_relative}/{entry.name}/events.jsonl"
                candidate = paths.root / relative
                try:
                    mode = candidate.lstat().st_mode
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(mode):
                    raise ValueError("canonical event ledger path contains a symlink")
                if not stat.S_ISREG(mode):
                    continue
                candidates.append(relative)
                if len(candidates) > self.max_ledger_files:
                    raise ValueError("canonical event ledger file limit exceeded")

        records: list[dict[str, Any]] = []
        total_size = 0
        for relative in sorted(candidates):
            remaining = self.max_ledger_bytes - total_size
            if remaining < 1:
                raise ValueError("canonical event ledger exceeds aggregate size limit")
            raw, _identity = self._read_project_file(
                paths.root,
                relative,
                max_bytes=remaining,
                label="canonical event ledger",
            )
            total_size += len(raw)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"canonical event ledger is not valid UTF-8: {relative}"
                ) from exc
            for number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{relative}:{number}: invalid canonical record JSON"
                    ) from exc
                if not isinstance(value, dict):
                    raise ValueError(
                        f"{relative}:{number}: canonical record must be an object"
                    )
                records.append(value)
                if len(records) > self.max_records:
                    raise ValueError("canonical event record limit exceeded")
        issues = validate_records(records)
        if issues:
            first = issues[0]
            raise ValueError(
                "canonical event validation failed: "
                f"{first.record_id}{first.path}: {first.message}"
            )
        return records

    def _record_views(
        self, records: list[dict[str, Any]], requested: list[str]
    ) -> dict[str, _RecordView]:
        by_id: dict[str, dict[str, Any]] = {}
        for record in records:
            record_id = str(record.get("record_id") or record.get("event_id") or "")
            if record_id in by_id:
                raise ValueError(f"duplicate canonical evidence record id: {record_id}")
            by_id[record_id] = record

        requested_set = set(requested)
        core_records = [
            record for record in records if record.get("schema_version") == "core/1.0"
        ]
        amendments_by_id = {
            str(record["record_id"]): record
            for record in core_records
            if record.get("record_kind") == "amendment"
        }
        for amendment in amendments_by_id.values():
            target = self._core_correction_target(amendment)
            status = str(amendment.get("status"))
            if target in requested_set and status in _PENDING_AMENDMENT_STATUSES:
                raise ValueError(
                    f"requested evidence has a pending amendment: {target}"
                )

        resolvable = [
            record
            for record in core_records
            if record.get("record_kind") != "amendment"
            or record.get("status") == "completed"
        ]
        resolved_core, applied = resolve_core_amendments(resolvable)
        resolved_by_id = {
            str(record["record_id"]): record
            for record in resolved_core
            if record.get("record_id")
        }
        applied_by_target: dict[str, list[dict[str, Any]]] = {}
        for item in applied:
            amendment = amendments_by_id[str(item["amendment_id"])]
            applied_by_target.setdefault(str(item["target_id"]), []).append(
                {
                    "amendment_id": item["amendment_id"],
                    "amendment_hash": artifact_hash(amendment),
                    "path": item["path"],
                    "status": amendment["status"],
                }
            )

        completed_superseders: dict[str, list[str]] = {}
        pending_superseders: dict[str, list[str]] = {}
        for record in records:
            relation_status = str(record.get("status") or "")
            for relation_type, target in self._authority_relations(record):
                if relation_type != "supersedes":
                    continue
                source_id = str(record.get("record_id") or record.get("event_id") or "")
                if relation_status == "completed":
                    completed_superseders.setdefault(target, []).append(source_id)
                elif relation_status not in _TERMINAL_IGNORED_AMENDMENT_STATUSES:
                    pending_superseders.setdefault(target, []).append(source_id)

        views: dict[str, _RecordView] = {}
        for record_id in requested:
            canonical = by_id.get(record_id)
            if canonical is None:
                continue
            if completed_superseders.get(record_id):
                raise ValueError(
                    "requested evidence was superseded: "
                    f"{record_id} by {', '.join(sorted(completed_superseders[record_id]))}"
                )
            if pending_superseders.get(record_id):
                raise ValueError(
                    "requested evidence has a pending supersession: " + record_id
                )

            if canonical.get("schema_version") == "core/1.0":
                resolved = resolved_by_id[record_id]
                self._require_eligible_core(record_id, resolved)
                views[record_id] = _RecordView(
                    record_id=record_id,
                    family="core/1.0",
                    canonical=canonical,
                    resolved=resolved,
                    canonical_hash=artifact_hash(canonical),
                    resolved_hash=artifact_hash(resolved),
                    applied_amendments=tuple(applied_by_target.get(record_id, [])),
                )
            else:
                self._require_eligible_legacy(record_id, canonical)
                if self._legacy_correction_targets(records, record_id):
                    raise ValueError(
                        "legacy evidence has amendment ambiguity and is not claim-eligible: "
                        + record_id
                    )
                digest = artifact_hash(canonical)
                views[record_id] = _RecordView(
                    record_id=record_id,
                    family="legacy",
                    canonical=canonical,
                    resolved=canonical,
                    canonical_hash=digest,
                    resolved_hash=digest,
                    applied_amendments=(),
                )
        return views

    @staticmethod
    def _core_correction_target(record: dict[str, Any]) -> str:
        targets = [
            relation.get("target_id")
            for relation in record.get("relations", [])
            if isinstance(relation, dict) and relation.get("type") == "corrects"
        ]
        if len(targets) != 1 or not isinstance(targets[0], str):
            raise ValueError(
                f"{record.get('record_id')}: amendment has an invalid corrects relation"
            )
        return targets[0]

    @staticmethod
    def _authority_relations(record: dict[str, Any]) -> list[tuple[str, str]]:
        relations = record.get("relations") or []
        if not isinstance(relations, list):
            return []
        output: list[tuple[str, str]] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            relation_type = relation.get("type")
            target = relation.get("target_id", relation.get("target"))
            if relation_type in {"corrects", "supersedes"}:
                if not isinstance(target, str) or not target:
                    status = str(record.get("status") or "")
                    if status not in _TERMINAL_IGNORED_AMENDMENT_STATUSES:
                        raise ValueError("canonical authority relation has no target")
                    continue
                output.append((relation_type, target))
        return output

    @classmethod
    def _legacy_correction_targets(
        cls, records: list[dict[str, Any]], record_id: str
    ) -> tuple[str, ...]:
        related: list[str] = []
        for record in records:
            if record.get("schema_version") == "core/1.0":
                continue
            status = str(record.get("status") or "")
            if status in _TERMINAL_IGNORED_AMENDMENT_STATUSES:
                continue
            for relation_type, target in cls._authority_relations(record):
                if relation_type in {"corrects", "supersedes"} and target == record_id:
                    related.append(str(record.get("event_id") or "<unknown>"))
        return tuple(related)

    @staticmethod
    def _require_eligible_core(record_id: str, record: dict[str, Any]) -> None:
        if record.get("record_kind") == "amendment":
            raise ValueError(f"amendment is not direct claim evidence: {record_id}")
        if record.get("status") not in _ELIGIBLE_CORE_STATUSES:
            raise ValueError(
                f"requested evidence record is not claim-eligible: {record_id}"
            )
        if record.get("status") == "superseded":
            raise ValueError(f"requested evidence record was superseded: {record_id}")
        if ProjectEvidenceBundleBuilder._record_is_withdrawn(record):
            raise ValueError(
                f"requested evidence claim was withdrawn or retracted: {record_id}"
            )

    @staticmethod
    def _require_eligible_legacy(record_id: str, record: dict[str, Any]) -> None:
        if record.get("status") != "completed":
            raise ValueError(f"legacy evidence must have completed status: {record_id}")
        if str(record.get("event_type") or "").lower() in {
            "amendment",
            "withdrawal",
            "retraction",
        }:
            raise ValueError(
                f"legacy authority record is not direct evidence: {record_id}"
            )
        if ProjectEvidenceBundleBuilder._record_is_withdrawn(record):
            raise ValueError(
                f"legacy evidence was withdrawn, retracted, or superseded: {record_id}"
            )

    @staticmethod
    def _record_is_withdrawn(record: dict[str, Any]) -> bool:
        if str(record.get("status") or "").lower() in _WITHDRAWN_MARKERS:
            return True
        payload = record.get("payload")
        candidates = [record]
        if isinstance(payload, dict):
            candidates.append(payload)
        for candidate in candidates:
            for key in ("support_status", "claim_status", "disposition", "state"):
                if str(candidate.get(key) or "").lower() in _WITHDRAWN_MARKERS:
                    return True
            if candidate.get("withdrawn") is True or candidate.get("retracted") is True:
                return True
        return False

    @staticmethod
    def _references(view: _RecordView) -> list[dict[str, Any]]:
        record = view.resolved
        if view.family == "core/1.0":
            if "source" in record:
                raise ValueError(
                    "core evidence cannot use the legacy top-level source field"
                )
            raw_references = record.get("source_refs") or []
            if not isinstance(raw_references, list):
                raise ValueError("canonical evidence source_refs must be an array")
            references: list[dict[str, Any]] = []
            for item in raw_references:
                if not isinstance(item, dict):
                    raise ValueError("canonical evidence source_ref must be an object")
                verification_status = item.get("verification_status")
                if verification_status not in _VERIFIED_SOURCE_STATUSES:
                    raise ValueError(
                        "canonical evidence source_ref is not integrity verified"
                    )
                locator = item.get("locator") or {}
                if not isinstance(locator, dict) or "path" not in locator:
                    raise ValueError(
                        "canonical evidence source_ref has no exact locator"
                    )
                revision = str(item.get("artifact_revision_id") or "")
                digest = (
                    revision.rsplit("@sha256:", 1)[-1]
                    if "@sha256:" in revision
                    else locator.get("source_sha256")
                )
                normalized = ProjectEvidenceBundleBuilder._normalize_reference(
                    {
                        "source_path": locator.get("path"),
                        "source_sha256": digest,
                        "line_start": locator.get("line_start", locator.get("start")),
                        "line_end": locator.get("line_end", locator.get("end")),
                    }
                )
                normalized["verification_status"] = verification_status
                normalized["artifact_revision_id"] = revision
                references.append(normalized)
            return references

        source = record.get("source")
        if not isinstance(source, dict):
            raise ValueError("legacy evidence requires one exact top-level source")
        normalized = ProjectEvidenceBundleBuilder._normalize_reference(source)
        normalized["verification_status"] = "legacy_exact_hash"
        normalized["artifact_revision_id"] = (
            f"legacy@sha256:{normalized['source_sha256'].lower()}"
        )
        return [normalized]

    @staticmethod
    def _normalize_reference(value: dict[str, Any]) -> dict[str, Any]:
        path = value.get("source_path")
        digest = value.get("source_sha256")
        start = value.get("line_start")
        end = value.get("line_end")
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or not isinstance(start, int)
            or isinstance(start, bool)
            or start < 1
            or not isinstance(end, int)
            or isinstance(end, bool)
            or end < start
        ):
            raise ValueError(
                "canonical evidence reference is not an exact path/hash/range"
            )
        return {
            "path": ProjectEvidenceBundleBuilder._normalize_relative_path(path),
            "source_sha256": digest.lower(),
            "line_start": start,
            "line_end": end,
        }

    @staticmethod
    def _authority_material(
        view: _RecordView,
        reference: dict[str, Any],
        registration: _SourceRegistration,
        source_identity_hash: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        return {
            "record_id": view.record_id,
            "record_family": view.family,
            "record_kind": view.resolved.get("record_kind")
            or view.resolved.get("event_type"),
            "record_status": view.resolved.get("status"),
            "canonical_record_hash": view.canonical_hash,
            "resolved_record_hash": view.resolved_hash,
            "current_view": {
                "is_amended": bool(view.applied_amendments),
                "applied_amendments": list(view.applied_amendments),
            },
            "source": {
                "evidence_ref": evidence_ref,
                "artifact_revision_id": reference["artifact_revision_id"],
                "verification_status": reference["verification_status"],
                "source_id": registration.source_id,
                "logical_path": registration.path,
                "resolved_path_identity_hash": source_identity_hash,
                "registration_hash": registration.registration_hash,
                "registration_identity_hash": registration.registration_identity_hash,
                "manifest_line": registration.manifest_line,
            },
        }

    @staticmethod
    def _scope_path_allowed(path: str, allowed: tuple[str, ...]) -> bool:
        return any(
            path == pattern.rstrip("/")
            or path.startswith(pattern.rstrip("/") + "/")
            or fnmatchcase(path, pattern)
            for pattern in allowed
        )

    @staticmethod
    def _scope_source_allowed(
        source_id: str, path: str, allowed: tuple[str, ...]
    ) -> bool:
        return any(
            source_id == pattern
            or path == pattern
            or fnmatchcase(source_id, pattern)
            or fnmatchcase(path, pattern)
            for pattern in allowed
        )

    @staticmethod
    def _normalize_relative_path(value: str) -> str:
        supplied = Path(value)
        if supplied.is_absolute() or not supplied.parts or ".." in supplied.parts:
            raise ValueError("evidence source path must remain project-relative")
        if any(part in {"", "."} for part in supplied.parts):
            raise ValueError("evidence source path must be canonical")
        return supplied.as_posix()

    @staticmethod
    def _validate_source_path(relative: str) -> None:
        supplied = Path(ProjectEvidenceBundleBuilder._normalize_relative_path(relative))
        lowered = [part.lower() for part in supplied.parts]
        if supplied.name.lower() in _DENIED_BASENAMES or any(
            fragment in part for part in lowered for fragment in _DENIED_FRAGMENTS
        ):
            raise ValueError("evidence source path is denied")

    @staticmethod
    def _reject_root_symlink_components(root: Path) -> None:
        absolute = root.expanduser().absolute()
        chain = list(reversed(absolute.parents)) + [absolute]
        for component in chain:
            try:
                mode = component.lstat().st_mode
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"project root component is missing: {component}"
                ) from exc
            if stat.S_ISLNK(mode):
                raise ValueError("project root path contains a symlink component")

    @staticmethod
    def _open_project_directory(root: Path, relative: str) -> None:
        parts = Path(
            ProjectEvidenceBundleBuilder._normalize_relative_path(relative)
        ).parts
        flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
        )
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        descriptors: list[int] = []
        try:
            current = os.open(root, flags | nofollow)
            descriptors.append(current)
            for part in parts:
                current = os.open(part, flags | nofollow, dir_fd=current)
                descriptors.append(current)
        except OSError as exc:
            raise ValueError(
                f"project evidence directory contains a symlink or is inaccessible: {relative}"
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _read_project_file(
        root: Path,
        relative: str,
        *,
        max_bytes: int,
        label: str,
    ) -> tuple[bytes, str]:
        """Read one regular file once through an O_NOFOLLOW descriptor chain."""

        normalized = ProjectEvidenceBundleBuilder._normalize_relative_path(relative)
        parts = Path(normalized).parts
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptors: list[int] = []
        try:
            current = os.open(root, directory_flags)
            descriptors.append(current)
            for part in parts[:-1]:
                current = os.open(part, directory_flags, dir_fd=current)
                descriptors.append(current)
            file_descriptor = os.open(parts[-1], file_flags, dir_fd=current)
            descriptors.append(file_descriptor)
            before = os.fstat(file_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"{label} is not a regular file: {normalized}")
            if before.st_size > max_bytes:
                raise ValueError(f"{label} exceeds size limit: {normalized}")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(file_descriptor, min(64 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"{label} exceeds size limit: {normalized}")
            after = os.fstat(file_descriptor)
            identity_before = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            identity_after = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
            if identity_before != identity_after or total != after.st_size:
                raise ValueError(f"{label} changed while being read: {normalized}")
            identity_hash = artifact_hash(
                {
                    "logical_path": normalized,
                    "resolved_relative_path": normalized,
                    "device": after.st_dev,
                    "inode": after.st_ino,
                    "size": after.st_size,
                    "mtime_ns": after.st_mtime_ns,
                }
            )
            return b"".join(chunks), identity_hash
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"{label} is missing: {normalized}") from exc
        except OSError as exc:
            raise ValueError(
                f"{label} path contains a symlink or is inaccessible: {normalized}"
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


__all__ = [
    "EvidenceBundle",
    "EvidenceBundleBuilder",
    "EvidencePassage",
    "ProjectEvidenceBundleBuilder",
    "evidence_snapshot_hash",
]
