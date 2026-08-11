"""Fail-closed, record-preserving failure policy for governance v2."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from typing import Any, Mapping

from universal_research_mcp.governance.hashing import artifact_hash


STOP_VALUES = frozenset({"always", "blocking_only", "current_step"})
RECORD_VALUES = frozenset({"full", "metadata_only", "ask"})
DETAIL_VALUES = frozenset({"full", "redacted", "hashes_only"})
DEFAULT_POLICY = {"stop": "blocking_only", "record": "ask", "detail": "redacted"}
ENV_KEYS = {
    "stop": "URAG_FAILURE_STOP_POLICY",
    "record": "URAG_FAILURE_RECORD_POLICY",
    "detail": "URAG_FAILURE_DETAIL_LEVEL",
}
# Read-only compatibility for profiles created during the v1 draft. New
# snapshots always report and document the URAG names above.
LEGACY_ENV_KEYS = {
    "stop": "UNIVERSAL_RESEARCH_FAILURE_STOP",
    "record": "UNIVERSAL_RESEARCH_FAILURE_RECORD",
    "detail": "UNIVERSAL_RESEARCH_FAILURE_DETAIL",
}
CLASSIFICATIONS = frozenset({
    "scientific_negative_result", "execution_failure", "validation_failure",
    "policy_violation", "evidence_failure", "user_cancelled",
    "expected_rejection",
})
CRITICAL_CLASSIFICATIONS = frozenset({
    "validation_failure", "policy_violation", "evidence_failure",
})
RESEARCH_RESULT_CLASSIFICATIONS = frozenset({
    "scientific_negative_result", "expected_rejection",
})
_CODE_CLASSIFICATION = {
    "GOV-APPROVAL": "policy_violation",
    "GOV-SCOPE": "policy_violation",
    "GOV-COST": "policy_violation",
    "GOV-OPTIN": "policy_violation",
    "GOV-OUTPUT": "validation_failure",
    "INTEGRITY": "validation_failure",
    "EVIDENCE": "evidence_failure",
    "VALIDATION": "validation_failure",
    "POLICY": "policy_violation",
    "CANCEL": "user_cancelled",
    "REJECT": "expected_rejection",
    "NEGATIVE": "scientific_negative_result",
    "RATE_LIMIT": "execution_failure",
    "NETWORK": "execution_failure",
    "DEPENDENCY": "execution_failure",
    "RESOURCE": "execution_failure",
    "PROVIDER": "execution_failure",
    "EXEC": "execution_failure",
}
_SENSITIVE = ("secret", "token", "credential", "password", "api_key", "apikey", "private_key")
_INLINE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)


def _policy_from_profile(profile: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(profile, Mapping):
        return {}
    direct = profile.get("failure_policy")
    if isinstance(direct, Mapping):
        return direct
    governance = profile.get("governance")
    if isinstance(governance, Mapping) and isinstance(governance.get("failure_policy"), Mapping):
        return governance["failure_policy"]
    return {}


def resolve_failure_policy(
    task: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve each policy field using task > profile > environment > defaults."""

    task_policy = task.get("failure_policy", {}) if isinstance(task, Mapping) else {}
    task_policy = task_policy if isinstance(task_policy, Mapping) else {}
    profile_policy = _policy_from_profile(profile)
    environment = os.environ if environ is None else environ
    allowed = {"stop": STOP_VALUES, "record": RECORD_VALUES, "detail": DETAIL_VALUES}
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for field in ("stop", "record", "detail"):
        candidates = (
            ("task", task_policy.get(field)),
            ("profile", profile_policy.get(field)),
            (f"env:{ENV_KEYS[field]}", environment.get(ENV_KEYS[field])),
            (f"env:{LEGACY_ENV_KEYS[field]}", environment.get(LEGACY_ENV_KEYS[field])),
            ("default", DEFAULT_POLICY[field]),
        )
        source, value = next((source, value) for source, value in candidates if value not in (None, ""))
        if value not in allowed[field]:
            raise ValueError(f"invalid failure policy {field}: {value}")
        values[field] = str(value)
        sources[field] = source
    return {
        "schema_version": "failure-policy/2.0",
        **values,
        "sources": sources,
        "minimum_tombstone_required": True,
        "retry_requires_reapproval_on_scope_change": True,
    }


def classify_failure(failure: Mapping[str, Any]) -> dict[str, Any]:
    """Classify from an explicit class or stable error-code prefix, never prose."""

    explicit = failure.get("classification")
    if explicit is not None and explicit not in CLASSIFICATIONS:
        raise ValueError(f"invalid failure classification: {explicit}")
    classification = str(explicit) if explicit else "execution_failure"
    code = str(failure.get("code") or "").upper()
    if explicit is None:
        classification = next(
            (value for prefix, value in _CODE_CLASSIFICATION.items() if code.startswith(prefix)),
            "execution_failure",
        )
    blocking = failure.get("blocking", True)
    if not isinstance(blocking, bool):
        raise ValueError("failure.blocking must be boolean")
    if classification in CRITICAL_CLASSIFICATIONS:
        blocking = True
    return {
        "classification": classification,
        "blocking": blocking,
        "code": code or "UNSPECIFIED",
        "is_research_result": classification in RESEARCH_RESULT_CLASSIFICATIONS,
    }


def failure_directive(failure: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    """Every failure stops immediately; policy selects the halted scope."""

    classified = classify_failure(failure)
    stop = policy.get("stop")
    if stop not in STOP_VALUES:
        raise ValueError("resolved failure policy is required")
    if classified["classification"] in CRITICAL_CLASSIFICATIONS or classified["classification"] == "user_cancelled":
        stop_scope = "workflow"
    elif classified["classification"] in RESEARCH_RESULT_CLASSIFICATIONS:
        stop_scope = "current_step"
    elif stop == "always" or (stop == "blocking_only" and classified["blocking"]):
        stop_scope = "workflow"
    else:
        stop_scope = "current_step"
    return {
        "immediate_stop": True,
        "block_new_operations": True,
        "shutdown_request_sequence": [
            "request_graceful_shutdown",
            "request_host_timeout_escalation_if_authorized",
            "isolate_partial_artifact",
        ],
        "shutdown_execution_status": "host_or_executor_must_report_actual_result",
        "force_termination_authority": "not_granted_by_policy_layer",
        "stop_scope": stop_scope,
        "continuation_policy": (
            "according_to_approved_research_plan"
            if classified["classification"] in RESEARCH_RESULT_CLASSIFICATIONS
            else "user_decision_or_approved_retry"
        ),
        "retry_allowed_before_record": False,
    }


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if any(fragment in str(key).lower() for fragment in _SENSITIVE) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return value


def build_failure_record(
    failure: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Build the mandatory tombstone and optional detail without writing storage."""

    classified = classify_failure(failure)
    directive = failure_directive(failure, policy)
    timestamp = occurred_at or datetime.now(timezone.utc).isoformat()
    raw_detail = failure.get("detail", failure.get("message", ""))
    safe_detail = _redact(raw_detail)
    detail_sha256 = artifact_hash(safe_detail)
    identity = {
        "run_id": str(failure.get("run_id") or "unknown"),
        "workflow_id": str(failure.get("workflow_id") or "unknown"),
        "agent_id": str(failure.get("agent_id") or "unknown"),
        "operation_id": str(failure.get("operation_id") or "unknown"),
    }
    failure_id = "failure_" + artifact_hash({**identity, "timestamp": timestamp, "code": classified["code"], "detail_sha256": detail_sha256}).split(":", 1)[1][:20]
    record_mode = policy.get("record")
    detail_mode = policy.get("detail")
    if record_mode not in RECORD_VALUES or detail_mode not in DETAIL_VALUES:
        raise ValueError("resolved failure policy is required")
    record: dict[str, Any] = {
        "schema_version": "failure-tombstone/2.0",
        "event_type": "stopped_work",
        "failure_id": failure_id,
        "occurred_at": timestamp,
        **identity,
        **classified,
        **directive,
        "record_policy": record_mode,
        "detail_policy": detail_mode,
        "detail_sha256": detail_sha256,
        "record_state": "awaiting_user_choice" if record_mode == "ask" else "recorded",
        "requires_user_choice": record_mode == "ask",
        "canonical_minimum_record_preserved": True,
    }
    if record_mode == "full":
        if detail_mode == "full":
            record["detail"] = safe_detail
        elif detail_mode == "redacted":
            record["detail"] = safe_detail
        else:
            record["detail_hashes"] = [detail_sha256]
    return record
