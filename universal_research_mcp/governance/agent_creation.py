"""Closed disclosure and approval declarations for governed agent creation.

This module does not authenticate a human.  It makes the explanation that was
shown to a user exact, hash-bound, and mandatory at every Universal execution
boundary.  A host-owned one-time approval store remains the authority that may
turn the declaration into execution permission.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, Mapping, Sequence

from universal_research_mcp.governance.hashing import artifact_hash


AGENT_CREATION_DISCLOSURE_VERSION = "agent-creation-disclosure/1.0"
AGENT_CREATION_OPT_IN = "agent_creation"
AGENT_CREATION_ISSUE = "GOV-AGENT-CREATION-001"
_DISCLOSURE_FIELDS = frozenset({
    "schema_version",
    "reason",
    "delegated_tasks",
    "agent_count",
    "direct_execution_alternative",
    "expected_additional_tokens",
    "expected_elapsed_minutes",
    "scope",
})
_RANGE_FIELDS = frozenset({"minimum", "likely", "maximum"})
_SCOPE_FIELDS = frozenset({"paths", "network", "model_execution", "writes"})


class AgentCreationDisclosureError(ValueError):
    """Raised when an agent-creation explanation is incomplete or ambiguous."""


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise AgentCreationDisclosureError(f"{label} must be a non-empty bounded string")
    return value.strip()


def _string_array(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise AgentCreationDisclosureError(f"{label} must be an array of bounded strings")
    normalized = [_text(item, label) for item in value]
    if len(normalized) != len(set(normalized)):
        raise AgentCreationDisclosureError(f"{label} must contain unique values")
    if len(normalized) > 64:
        raise AgentCreationDisclosureError(f"{label} exceeds the maximum item count")
    return normalized


def _number_range(
    value: object,
    label: str,
    *,
    integer: bool,
    maximum: float,
) -> dict[str, int | float]:
    if not isinstance(value, Mapping) or set(value) != _RANGE_FIELDS:
        raise AgentCreationDisclosureError(
            f"{label} must contain exactly minimum, likely, and maximum",
        )
    normalized: dict[str, int | float] = {}
    for field in ("minimum", "likely", "maximum"):
        item = value.get(field)
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not isfinite(float(item))
            or item < 0
            or item > maximum
            or integer and not isinstance(item, int)
        ):
            kind = "integer" if integer else "number"
            raise AgentCreationDisclosureError(f"{label}.{field} must be a bounded {kind}")
        normalized[field] = int(item) if integer else float(item)
    if not (
        normalized["minimum"] <= normalized["likely"] <= normalized["maximum"]
    ):
        raise AgentCreationDisclosureError(f"{label} must be monotonic")
    return normalized


def normalize_agent_creation_disclosure(
    value: object,
    *,
    expected_agent_count: int | None = None,
) -> dict[str, Any]:
    """Return one exact disclosure or fail before any governed agent exists."""

    if not isinstance(value, Mapping) or set(value) != _DISCLOSURE_FIELDS:
        raise AgentCreationDisclosureError("agent creation disclosure has an unsupported shape")
    if value.get("schema_version") != AGENT_CREATION_DISCLOSURE_VERSION:
        raise AgentCreationDisclosureError("agent creation disclosure schema is unsupported")
    count = value.get("agent_count")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
        or count > 16
    ):
        raise AgentCreationDisclosureError("agent_count must be in [1, 16]")
    if expected_agent_count is not None and count != expected_agent_count:
        raise AgentCreationDisclosureError("agent_count does not match the requested agents")
    tasks = _string_array(value.get("delegated_tasks"), "delegated_tasks")
    if len(tasks) != count:
        raise AgentCreationDisclosureError("delegated_tasks must describe each requested agent")
    scope = value.get("scope")
    if not isinstance(scope, Mapping) or set(scope) != _SCOPE_FIELDS:
        raise AgentCreationDisclosureError("agent creation scope has an unsupported shape")
    booleans = {field: scope.get(field) for field in ("network", "model_execution", "writes")}
    if any(not isinstance(item, bool) for item in booleans.values()):
        raise AgentCreationDisclosureError("agent creation scope flags must be boolean")
    if booleans["model_execution"] is not True:
        raise AgentCreationDisclosureError("agent creation must disclose model execution")
    return {
        "schema_version": AGENT_CREATION_DISCLOSURE_VERSION,
        "reason": _text(value.get("reason"), "reason"),
        "delegated_tasks": tasks,
        "agent_count": count,
        "direct_execution_alternative": _text(
            value.get("direct_execution_alternative"),
            "direct_execution_alternative",
        ),
        "expected_additional_tokens": _number_range(
            value.get("expected_additional_tokens"),
            "expected_additional_tokens",
            integer=True,
            maximum=100_000_000,
        ),
        "expected_elapsed_minutes": _number_range(
            value.get("expected_elapsed_minutes"),
            "expected_elapsed_minutes",
            integer=False,
            maximum=100_000,
        ),
        "scope": {
            "paths": _string_array(scope.get("paths"), "scope.paths", allow_empty=True),
            **booleans,
        },
    }


def agent_creation_disclosure_hash(value: object, *, expected_agent_count: int | None = None) -> str:
    return artifact_hash(normalize_agent_creation_disclosure(
        value,
        expected_agent_count=expected_agent_count,
    ))


def validate_agent_creation_packets(
    packets: Sequence[dict[str, Any]],
    *,
    expected_agent_count: int | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    """Require one shared explanation and explicit declared user approval.

    This is a proposal/execution-contract check, not proof of user presence.
    Provider and secure-harness execution must additionally consume a host-owned
    one-time approval before the first model process or request is created.
    """

    issues: list[dict[str, str]] = []
    if not packets:
        return [{"code": AGENT_CREATION_ISSUE, "message": "agent packet batch is empty"}], None
    normalized: list[dict[str, Any]] = []
    for packet in packets:
        try:
            normalized.append(normalize_agent_creation_disclosure(
                packet.get("agent_creation_disclosure") if isinstance(packet, dict) else None,
                expected_agent_count=expected_agent_count,
            ))
        except AgentCreationDisclosureError as exc:
            issues.append({"code": AGENT_CREATION_ISSUE, "message": str(exc)})
            continue
        authority = packet.get("authority")
        if not isinstance(authority, Mapping):
            issues.append({"code": AGENT_CREATION_ISSUE, "message": "agent creation authority is missing"})
            continue
        approval_refs = authority.get("approval_refs")
        if (
            not isinstance(approval_refs, list)
            or not approval_refs
            or any(not isinstance(item, str) or not item for item in approval_refs)
        ):
            issues.append({
                "code": AGENT_CREATION_ISSUE,
                "message": "agent creation requires an explicit user approval reference",
            })
        opt_ins = authority.get("user_opt_ins")
        if (
            not isinstance(opt_ins, list)
            or AGENT_CREATION_OPT_IN not in opt_ins
        ):
            issues.append({
                "code": AGENT_CREATION_ISSUE,
                "message": "agent creation requires the explicit agent_creation user opt-in",
            })
    if normalized and any(item != normalized[0] for item in normalized[1:]):
        issues.append({
            "code": AGENT_CREATION_ISSUE,
            "message": "all requested agents must share the exact approved disclosure",
        })
    common_approvals: set[str] | None = None
    for packet in packets:
        authority = packet.get("authority") if isinstance(packet, dict) else None
        raw_references = (authority or {}).get("approval_refs")
        references = {
            item for item in raw_references
            if isinstance(item, str) and item
        } if isinstance(raw_references, list) else set()
        common_approvals = references if common_approvals is None else common_approvals & references
    if not common_approvals:
        issues.append({
            "code": AGENT_CREATION_ISSUE,
            "message": "agent packets do not share one explicit approval reference",
        })
    return issues, deepcopy(normalized[0]) if normalized and not issues else None


__all__ = [
    "AGENT_CREATION_DISCLOSURE_VERSION",
    "AGENT_CREATION_OPT_IN",
    "AgentCreationDisclosureError",
    "agent_creation_disclosure_hash",
    "normalize_agent_creation_disclosure",
    "validate_agent_creation_packets",
]
