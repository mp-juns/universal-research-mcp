"""Create-only runtime artifacts and a hash-chained append event ledger."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Iterator

from universal_research_mcp.governance.hashing import artifact_hash, canonical_json, hash_without
from universal_research_mcp.runtime import ProjectPaths

try:  # pragma: no cover - Windows is not a supported execution host today.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RUNS_PARTS = ("data", "governance", "runs")
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_MAX_LEDGER_BYTES = 16 * 1024 * 1024
_MAX_EVENTS = 100_000
_MAX_SESSIONS = 64
_MAX_RECEIPTS = 256
_MAX_EVENT_REFS = 32
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
RUN_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"created"}),
    "created": frozenset({"materialized", "stopping"}),
    "materialized": frozenset({"preflight_passed", "stopping"}),
    "preflight_passed": frozenset({"governor_running", "stopping"}),
    "governor_running": frozenset({"governed", "stopping"}),
    "governed": frozenset({"workers_running", "completed", "stopping"}),
    "workers_running": frozenset({"completed", "stopping"}),
    "stopping": frozenset({"blocked", "cancelled"}),
    "completed": frozenset(),
    "blocked": frozenset(),
    "cancelled": frozenset(),
}
SESSION_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"created"}),
    "created": frozenset({"packet_validated", "stop_requested"}),
    "packet_validated": frozenset({"prompt_bound", "stop_requested"}),
    "prompt_bound": frozenset({"dispatch_reserved", "stop_requested"}),
    "dispatch_reserved": frozenset({"running", "stop_requested"}),
    "running": frozenset({"output_received", "stop_requested"}),
    "output_received": frozenset({"decision_validated", "stop_requested"}),
    "decision_validated": frozenset({"completed", "stop_requested"}),
    "stop_requested": frozenset({"failed", "blocked"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "blocked": frozenset(),
}


class RuntimeStoreError(RuntimeError):
    """Raised when immutable storage or ledger integrity would be weakened."""


def _safe_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or ".." in value:
        raise ValueError(f"invalid {label}")
    return value


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise RuntimeStoreError("runtime artifact write made no progress")
        offset += written


class SessionStore:
    """Own immutable run/session JSON and one append-only event stream per run."""

    def __init__(self, root: str | Path) -> None:
        paths = ProjectPaths.from_root(root)
        self.root = paths.root
        self.runs_root = paths.resolve_relative("data/governance/runs")
        self._thread_lock = threading.RLock()

    def run_dir(self, run_id: str) -> Path:
        return self.runs_root / _safe_identifier(run_id, "run_id")

    def session_dir(self, run_id: str, session_id: str) -> Path:
        return self.run_dir(run_id) / "sessions" / _safe_identifier(session_id, "session_id")

    def create_run(self, run_id: str, manifest: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        run_id = _safe_identifier(run_id, "run_id")
        with self._open_runs_root(create=True) as runs_fd:
            try:
                os.mkdir(run_id, mode=0o700, dir_fd=runs_fd)
            except FileExistsError as exc:
                raise RuntimeStoreError(f"run already exists: {run_id}") from exc
            with self._open_child_directory(runs_fd, run_id) as run_fd:
                os.mkdir("sessions", mode=0o700, dir_fd=run_fd)
                os.mkdir("receipts", mode=0o700, dir_fd=run_fd)
                run_ref = self._create_json_at(
                    run_fd, "run.json", manifest, self._relative_ref(run_id, "run.json"),
                )
                plan_ref = self._create_json_at(
                    run_fd, "run-plan.json", plan, self._relative_ref(run_id, "run-plan.json"),
                )
                self._create_bytes_at(run_fd, "events.jsonl", b"")
                self._create_bytes_at(run_fd, ".events.lock", b"")
                os.fsync(run_fd)
        return {"run": run_ref, "plan": plan_ref}

    def create_session(
        self,
        run_id: str,
        session_id: str,
        *,
        agent_id: str,
        manifest: dict[str, Any],
        task: dict[str, Any],
        evidence: dict[str, Any],
        prompt_template: dict[str, Any],
    ) -> list[dict[str, str]]:
        run_id = _safe_identifier(run_id, "run_id")
        session_id = _safe_identifier(session_id, "session_id")
        with self._open_run_directory(run_id) as run_fd:
            with self._open_child_directory(run_fd, "sessions") as sessions_fd:
                if len(os.listdir(sessions_fd)) >= _MAX_SESSIONS:
                    raise RuntimeStoreError("runtime session limit exceeded")
                try:
                    os.mkdir(session_id, mode=0o700, dir_fd=sessions_fd)
                except FileExistsError as exc:
                    raise RuntimeStoreError(f"session already exists: {session_id}") from exc
                with self._open_child_directory(sessions_fd, session_id) as session_fd:
                    refs = [
                        self._create_json_at(
                            session_fd, "session.json", manifest,
                            self._relative_ref(run_id, "sessions", session_id, "session.json"),
                        ),
                        self._create_json_at(
                            session_fd, "task.json", task,
                            self._relative_ref(run_id, "sessions", session_id, "task.json"),
                        ),
                        self._create_json_at(
                            session_fd, "evidence.json", evidence,
                            self._relative_ref(run_id, "sessions", session_id, "evidence.json"),
                        ),
                        self._create_json_at(
                            session_fd, "prompt-template.json", prompt_template,
                            self._relative_ref(
                                run_id, "sessions", session_id, "prompt-template.json",
                            ),
                        ),
                    ]
                    os.fsync(session_fd)
                    return refs

    def create_session_artifact(
        self, run_id: str, session_id: str, name: str, value: dict[str, Any],
    ) -> dict[str, str]:
        if name not in {"prompt.json", "dispatch.json", "decision.json", "failure.json", "raw-output.json"}:
            raise ValueError("unsupported session artifact name")
        run_id = _safe_identifier(run_id, "run_id")
        session_id = _safe_identifier(session_id, "session_id")
        with self._open_session_directory(run_id, session_id) as session_fd:
            return self._create_json_at(
                session_fd, name, value,
                self._relative_ref(run_id, "sessions", session_id, name),
            )

    def create_receipt(self, run_id: str, value: dict[str, Any]) -> dict[str, str]:
        run_id = _safe_identifier(run_id, "run_id")
        digest = artifact_hash(value).split(":", 1)[1]
        name = f"{digest}.json"
        with self._open_run_directory(run_id) as run_fd:
            with self._open_child_directory(run_fd, "receipts") as receipts_fd:
                if len(os.listdir(receipts_fd)) >= _MAX_RECEIPTS:
                    raise RuntimeStoreError("runtime receipt limit exceeded")
                return self._create_json_at(
                    receipts_fd, name, value, self._relative_ref(run_id, "receipts", name),
                )

    def create_seal(self, run_id: str, run_plan_hash: str) -> dict[str, str]:
        """Seal a terminal local ledger so later tail changes fail closed."""

        with self._thread_lock, self._file_lock(run_id) as run_fd:
            events = self._read_and_verify_at(
                run_fd, run_id, allow_unsealed_terminal=True,
            )
            states = self._states(events)
            terminal = states.get("run")
            if terminal not in {"completed", "blocked", "cancelled"}:
                raise RuntimeStoreError("only a terminal run can be sealed")
            material = {
                "schema_version": "agent-runtime-run-seal/1.0",
                "run_id": run_id,
                "terminal_state": terminal,
                "event_count": len(events),
                "event_head_hash": events[-1]["event_hash"],
                "run_plan_hash": run_plan_hash,
            }
            material["seal_hash"] = hash_without(material, "seal_hash")
            return self._create_json_at(
                run_fd, "run-seal.json", material,
                self._relative_ref(run_id, "run-seal.json"),
            )

    def append_transition(
        self,
        run_id: str,
        *,
        scope: str,
        to_state: str,
        event_type: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        artifact_refs: list[dict[str, str]] | None = None,
        operation: str,
        decision: str,
        authority_basis: str,
        summary: str,
    ) -> dict[str, Any]:
        if scope not in {"run", "session"}:
            raise ValueError("transition scope must be run or session")
        if scope == "session" and (not session_id or not agent_id):
            raise ValueError("session transition requires session_id and agent_id")
        run_id = _safe_identifier(run_id, "run_id")
        refs = artifact_refs or []
        if len(refs) > _MAX_EVENT_REFS:
            raise RuntimeStoreError("runtime event artifact reference limit exceeded")
        with self._thread_lock, self._file_lock(run_id) as run_fd:
            events = self._read_and_verify_at(run_fd, run_id)
            if len(events) >= _MAX_EVENTS:
                raise RuntimeStoreError("runtime event limit exceeded")
            states = self._states(events)
            key = "run" if scope == "run" else str(session_id)
            current = states.get(key)
            transitions = RUN_TRANSITIONS if scope == "run" else SESSION_TRANSITIONS
            if to_state not in transitions.get(current, frozenset()):
                raise RuntimeStoreError(f"illegal {scope} transition: {current} -> {to_state}")
            sequence = len(events) + 1
            previous = events[-1]["event_hash"] if events else None
            material = {
                "schema_version": "agent-runtime-event/1.0",
                "sequence": sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "event_scope": scope,
                "event_type": event_type,
                "from_state": current,
                "to_state": to_state,
                "previous_event_hash": previous,
                "user_visible_summary": summary,
                "internal_artifact_refs": [f"{item['path']}#{item['sha256']}" for item in refs],
                "commands_or_operations": [operation],
                "decision": decision,
                "authority_basis": authority_basis,
                "chat_disclosure": {
                    "mode": "summary_only",
                    "reason": "default central manager disclosure policy",
                },
            }
            material["event_id"] = "runtime_event_" + artifact_hash(material).split(":", 1)[1][:24]
            material["event_hash"] = hash_without(material, "event_hash")
            payload = (canonical_json(material) + "\n").encode("utf-8")
            descriptor = self._open_regular_file_at(
                run_fd, "events.jsonl", os.O_RDWR | os.O_APPEND,
            )
            initial_size = os.fstat(descriptor).st_size
            if initial_size + len(payload) > _MAX_LEDGER_BYTES:
                os.close(descriptor)
                raise RuntimeStoreError("runtime ledger size limit exceeded")
            try:
                _write_all(descriptor, payload)
                os.fsync(descriptor)
            except Exception:
                os.ftruncate(descriptor, initial_size)
                os.fsync(descriptor)
                raise
            finally:
                os.close(descriptor)
            return material

    def read_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._thread_lock, self._file_lock(run_id) as run_fd:
            return self._read_and_verify_at(run_fd, run_id)

    def status(self, run_id: str) -> dict[str, Any]:
        events = self.read_events(run_id)
        states = self._states(events)
        return {
            "run_id": run_id,
            "state": states.get("run"),
            "sessions": {key: value for key, value in states.items() if key != "run"},
            "event_count": len(events),
            "event_head_hash": events[-1]["event_hash"] if events else None,
        }

    def inspect(self, run_id: str, agent_id: str | None = None) -> dict[str, Any]:
        run_id = _safe_identifier(run_id, "run_id")
        with self._thread_lock, self._file_lock(run_id) as run_fd:
            events = self._read_and_verify_at(run_fd, run_id)
            states = self._states(events)
            status = {
                "run_id": run_id,
                "state": states.get("run"),
                "sessions": {key: value for key, value in states.items() if key != "run"},
                "event_count": len(events),
                "event_head_hash": events[-1]["event_hash"] if events else None,
            }
            sessions: list[dict[str, Any]] = []
            manifest = self._read_json_at(run_fd, "run.json")
            plan = self._read_json_at(run_fd, "run-plan.json")
            with self._open_child_directory(run_fd, "sessions") as sessions_fd:
                names = sorted(os.listdir(sessions_fd))
                if len(names) > _MAX_SESSIONS:
                    raise RuntimeStoreError("runtime session limit exceeded")
                for name in names:
                    session_id = _safe_identifier(name, "session_id")
                    with self._open_child_directory(sessions_fd, session_id) as session_fd:
                        session = self._read_json_at(session_fd, "session.json")
                        if agent_id is not None and session.get("agent_id") != agent_id:
                            continue
                        decision_summary = None
                        if self._entry_exists_at(session_fd, "decision.json"):
                            envelope = self._read_json_at(session_fd, "decision.json")
                            decision = envelope.get("decision") or {}
                            findings = decision.get("findings") or []
                            evidence_refs = decision.get("evidence_refs") or []
                            decision_summary = {
                                "status": decision.get("status"),
                                "decision_hash": artifact_hash(envelope),
                                "finding_count": len(findings) if isinstance(findings, list) else 0,
                                "evidence_reference_count": (
                                    len(evidence_refs) if isinstance(evidence_refs, list) else 0
                                ),
                            }
                        artifact_names = self._regular_file_names(session_fd)
                        sessions.append({
                            "session_id": session.get("session_id"),
                            "agent_id": session.get("agent_id"),
                            "artifact_names": artifact_names,
                            "decision": decision_summary,
                        })
            return {"status": status, "manifest": manifest, "run_plan": plan, "sessions": sessions}

    @contextmanager
    def _file_lock(self, run_id: str) -> Iterator[int]:
        if fcntl is None:
            raise RuntimeStoreError("cross-process runtime ledger locking is unavailable")
        with self._open_run_directory(run_id) as run_fd:
            descriptor = self._open_regular_file_at(run_fd, ".events.lock", os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                try:
                    yield run_fd
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _states(events: list[dict[str, Any]]) -> dict[str, str]:
        states: dict[str, str] = {}
        for event in events:
            key = "run" if event["event_scope"] == "run" else str(event["session_id"])
            states[key] = str(event["to_state"])
        return states

    def _read_and_verify(
        self, run_id: str, *, allow_unsealed_terminal: bool = False,
    ) -> list[dict[str, Any]]:
        run_id = _safe_identifier(run_id, "run_id")
        with self._open_run_directory(run_id) as run_fd:
            return self._read_and_verify_at(
                run_fd, run_id, allow_unsealed_terminal=allow_unsealed_terminal,
            )

    def _read_and_verify_at(
        self,
        run_fd: int,
        run_id: str,
        *,
        allow_unsealed_terminal: bool = False,
    ) -> list[dict[str, Any]]:
        payload = self._read_bytes_at(
            run_fd, "events.jsonl", maximum_bytes=_MAX_LEDGER_BYTES,
        )
        events: list[dict[str, Any]] = []
        previous: str | None = None
        states: dict[str, str] = {}
        try:
            lines = payload.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise RuntimeStoreError("runtime ledger is not valid UTF-8") from exc
        if len(lines) > _MAX_EVENTS:
            raise RuntimeStoreError("runtime event limit exceeded")
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeStoreError(f"runtime ledger line {number} is invalid JSON") from exc
            if not isinstance(event, dict) or event.get("schema_version") != "agent-runtime-event/1.0":
                raise RuntimeStoreError(f"runtime ledger line {number} has an invalid schema")
            self._validate_event_shape(event, number)
            if event.get("run_id") != run_id:
                raise RuntimeStoreError(f"runtime ledger run identity mismatch at line {number}")
            if event.get("sequence") != len(events) + 1 or event.get("previous_event_hash") != previous:
                raise RuntimeStoreError(f"runtime ledger chain mismatch at line {number}")
            if event.get("event_hash") != hash_without(event, "event_hash"):
                raise RuntimeStoreError(f"runtime ledger hash mismatch at line {number}")
            scope = event.get("event_scope")
            key = "run" if scope == "run" else str(event.get("session_id"))
            current = states.get(key)
            transitions = RUN_TRANSITIONS if scope == "run" else SESSION_TRANSITIONS
            if event.get("from_state") != current or event.get("to_state") not in transitions.get(current, frozenset()):
                raise RuntimeStoreError(f"runtime ledger state mismatch at line {number}")
            states[key] = str(event["to_state"])
            previous = str(event["event_hash"])
            events.append(event)
        self._verify_artifact_refs(run_fd, run_id, events)
        self._verify_seal(
            run_fd, run_id, events, allow_unsealed_terminal=allow_unsealed_terminal,
        )
        return events

    @staticmethod
    def _validate_event_shape(event: dict[str, Any], number: int) -> None:
        expected = {
            "schema_version", "sequence", "timestamp", "run_id", "session_id", "agent_id",
            "event_scope", "event_type", "from_state", "to_state", "previous_event_hash",
            "user_visible_summary", "internal_artifact_refs", "commands_or_operations",
            "decision", "authority_basis", "chat_disclosure", "event_id", "event_hash",
        }
        if set(event) != expected:
            raise RuntimeStoreError(f"runtime ledger line {number} fields do not match the event contract")
        if not isinstance(event["sequence"], int) or isinstance(event["sequence"], bool):
            raise RuntimeStoreError(f"runtime ledger line {number} sequence is invalid")
        for field in (
            "timestamp", "run_id", "event_type", "to_state", "user_visible_summary",
            "decision", "authority_basis", "event_id", "event_hash",
        ):
            if not isinstance(event[field], str) or not event[field]:
                raise RuntimeStoreError(f"runtime ledger line {number} {field} is invalid")
        try:
            timestamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeStoreError(f"runtime ledger line {number} timestamp is invalid") from exc
        if timestamp.tzinfo is None:
            raise RuntimeStoreError(f"runtime ledger line {number} timestamp lacks timezone")
        scope = event["event_scope"]
        if scope == "run":
            if event["session_id"] is not None or event["agent_id"] is not None:
                raise RuntimeStoreError(f"runtime ledger line {number} run identity is invalid")
        elif scope == "session":
            if not isinstance(event["session_id"], str) or not isinstance(event["agent_id"], str):
                raise RuntimeStoreError(f"runtime ledger line {number} session identity is invalid")
        else:
            raise RuntimeStoreError(f"runtime ledger line {number} scope is invalid")
        if not isinstance(event["internal_artifact_refs"], list) or not isinstance(
            event["commands_or_operations"], list
        ) or not event["commands_or_operations"]:
            raise RuntimeStoreError(f"runtime ledger line {number} operations or refs are invalid")
        if (
            len(event["internal_artifact_refs"]) > _MAX_EVENT_REFS
            or len(event["commands_or_operations"]) > 8
            or len(event["user_visible_summary"]) > 2_048
            or len(event["decision"]) > 2_048
            or len(event["authority_basis"]) > 2_048
        ):
            raise RuntimeStoreError(f"runtime ledger line {number} exceeds resource limits")
        disclosure = event["chat_disclosure"]
        if not isinstance(disclosure, dict) or set(disclosure) != {"mode", "reason"}:
            raise RuntimeStoreError(f"runtime ledger line {number} disclosure is invalid")
        identifier_material = {
            key: value
            for key, value in event.items()
            if key not in {"event_id", "event_hash"}
        }
        expected_id = "runtime_event_" + artifact_hash(identifier_material).split(":", 1)[1][:24]
        if event["event_id"] != expected_id:
            raise RuntimeStoreError(f"runtime ledger event_id mismatch at line {number}")

    def _verify_artifact_refs(
        self, run_fd: int, run_id: str, events: list[dict[str, Any]],
    ) -> None:
        for event in events:
            for reference in event["internal_artifact_refs"]:
                if not isinstance(reference, str) or "#sha256:" not in reference:
                    raise RuntimeStoreError("runtime event artifact ref is invalid")
                relative, digest = reference.rsplit("#", 1)
                value = self._read_run_reference(run_fd, run_id, relative)
                if artifact_hash(value) != digest:
                    raise RuntimeStoreError(f"runtime artifact hash mismatch: {relative}")

    def _verify_seal(
        self,
        run_fd: int,
        run_id: str,
        events: list[dict[str, Any]],
        *,
        allow_unsealed_terminal: bool,
    ) -> None:
        states = self._states(events)
        terminal = states.get("run") in {"completed", "blocked", "cancelled"}
        if not self._entry_exists_at(run_fd, "run-seal.json"):
            if terminal and not allow_unsealed_terminal:
                raise RuntimeStoreError("terminal runtime ledger is missing its seal")
            return
        seal = self._read_json_at(run_fd, "run-seal.json")
        expected_fields = {
            "schema_version", "run_id", "terminal_state", "event_count",
            "event_head_hash", "run_plan_hash", "seal_hash",
        }
        if set(seal) != expected_fields or seal.get("schema_version") != "agent-runtime-run-seal/1.0":
            raise RuntimeStoreError("runtime seal contract is invalid")
        plan = self._read_json_at(run_fd, "run-plan.json")
        if (
            not terminal
            or seal.get("run_id") != run_id
            or seal.get("terminal_state") != states.get("run")
            or seal.get("event_count") != len(events)
            or seal.get("event_head_hash") != (events[-1]["event_hash"] if events else None)
            or seal.get("run_plan_hash") != plan.get("run_plan_hash")
            or seal.get("seal_hash") != hash_without(seal, "seal_hash")
        ):
            raise RuntimeStoreError("runtime seal does not match the ledger tail")

    @contextmanager
    def _open_runs_root(self, *, create: bool) -> Iterator[int]:
        descriptor = os.open(self.root, _DIRECTORY_FLAGS | _NOFOLLOW)
        try:
            for part in _RUNS_PARTS:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                try:
                    child = os.open(
                        part, _DIRECTORY_FLAGS | _NOFOLLOW, dir_fd=descriptor,
                    )
                except FileNotFoundError as exc:
                    raise RuntimeStoreError("runtime runs root does not exist") from exc
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise RuntimeStoreError(
                            "runtime runs path contains a symlink or non-directory",
                        ) from exc
                    raise
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def _open_child_directory(self, parent_fd: int, name: str) -> Iterator[int]:
        try:
            descriptor = os.open(
                name, _DIRECTORY_FLAGS | _NOFOLLOW, dir_fd=parent_fd,
            )
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise RuntimeStoreError(
                    "runtime artifact path contains a symlink or non-directory",
                ) from exc
            raise
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def _open_run_directory(self, run_id: str) -> Iterator[int]:
        run_id = _safe_identifier(run_id, "run_id")
        with self._open_runs_root(create=False) as runs_fd:
            try:
                with self._open_child_directory(runs_fd, run_id) as run_fd:
                    yield run_fd
            except FileNotFoundError as exc:
                raise RuntimeStoreError(f"unknown run: {run_id}") from exc

    @contextmanager
    def _open_session_directory(self, run_id: str, session_id: str) -> Iterator[int]:
        session_id = _safe_identifier(session_id, "session_id")
        with self._open_run_directory(run_id) as run_fd:
            with self._open_child_directory(run_fd, "sessions") as sessions_fd:
                try:
                    with self._open_child_directory(sessions_fd, session_id) as session_fd:
                        yield session_fd
                except FileNotFoundError as exc:
                    raise RuntimeStoreError(f"unknown session: {session_id}") from exc

    @staticmethod
    def _open_regular_file_at(parent_fd: int, name: str, flags: int) -> int:
        try:
            descriptor = os.open(
                name, flags | _NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd,
            )
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise RuntimeStoreError("runtime artifact path contains a symlink") from exc
            raise
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            os.close(descriptor)
            raise RuntimeStoreError(f"runtime artifact is not a regular file: {name}")
        return descriptor

    @staticmethod
    def _entry_exists_at(parent_fd: int, name: str) -> bool:
        try:
            descriptor = SessionStore._open_regular_file_at(parent_fd, name, os.O_RDONLY)
        except FileNotFoundError:
            return False
        else:
            os.close(descriptor)
            return True

    @staticmethod
    def _read_bytes_at(parent_fd: int, name: str, *, maximum_bytes: int) -> bytes:
        descriptor = SessionStore._open_regular_file_at(parent_fd, name, os.O_RDONLY)
        try:
            size = os.fstat(descriptor).st_size
            if size > maximum_bytes:
                raise RuntimeStoreError(f"runtime artifact exceeds size limit: {name}")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum_bytes:
                    raise RuntimeStoreError(f"runtime artifact exceeds size limit: {name}")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_json_at(parent_fd: int, name: str) -> dict[str, Any]:
        payload = SessionStore._read_bytes_at(
            parent_fd, name, maximum_bytes=_MAX_ARTIFACT_BYTES,
        )
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeStoreError(f"runtime artifact is invalid JSON: {name}") from exc
        if not isinstance(value, dict):
            raise RuntimeStoreError(f"runtime artifact must be an object: {name}")
        return value

    @staticmethod
    def _create_bytes_at(parent_fd: int, name: str, payload: bytes) -> None:
        if len(payload) > _MAX_ARTIFACT_BYTES:
            raise RuntimeStoreError(f"runtime artifact exceeds size limit: {name}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise RuntimeStoreError(f"artifact already exists: {name}") from exc
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        except Exception:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
        finally:
            os.close(descriptor)

    def _create_json_at(
        self,
        parent_fd: int,
        name: str,
        value: dict[str, Any],
        relative_reference: str,
    ) -> dict[str, str]:
        payload = (canonical_json(value) + "\n").encode("utf-8")
        self._create_bytes_at(parent_fd, name, payload)
        return {"path": relative_reference, "sha256": artifact_hash(value)}

    @staticmethod
    def _regular_file_names(parent_fd: int) -> list[str]:
        names: list[str] = []
        for name in os.listdir(parent_fd):
            try:
                descriptor = SessionStore._open_regular_file_at(parent_fd, name, os.O_RDONLY)
            except (IsADirectoryError, FileNotFoundError):
                continue
            else:
                os.close(descriptor)
                names.append(name)
        return sorted(names)

    @staticmethod
    def _relative_ref(run_id: str, *parts: str) -> str:
        return Path(*_RUNS_PARTS, run_id, *parts).as_posix()

    def _read_run_reference(self, run_fd: int, run_id: str, relative: str) -> dict[str, Any]:
        path = Path(relative)
        expected_prefix = (*_RUNS_PARTS, run_id)
        if path.is_absolute() or path.parts[: len(expected_prefix)] != expected_prefix:
            raise RuntimeStoreError("runtime artifact reference escapes its run")
        tail = path.parts[len(expected_prefix):]
        if not tail or any(part in {"", ".", ".."} for part in tail):
            raise RuntimeStoreError("runtime artifact reference is invalid")
        descriptor = os.dup(run_fd)
        try:
            for part in tail[:-1]:
                child = os.open(
                    part, _DIRECTORY_FLAGS | _NOFOLLOW, dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
            return self._read_json_at(descriptor, tail[-1])
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise RuntimeStoreError("runtime artifact reference contains a symlink") from exc
            raise
        finally:
            os.close(descriptor)


__all__ = ["RuntimeStoreError", "SessionStore"]
