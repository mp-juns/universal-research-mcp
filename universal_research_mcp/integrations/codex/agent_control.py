"""Fail-closed boundary for Codex host-agent coordination.

The tested Codex 0.147.0 stdio MCP contract has no documented or validated
authoritative per-call thread identity or proposal-bound, single-use user
approval receipt.  An environment variable, an echoed proposal hash, endpoint
locality alone, or a same-user CLI process cannot supply that missing
authority.

The public package therefore exposes an explicit unavailable status and never
changes Codex configuration or interrupts a turn.  A future host integration
may replace this boundary only when a protected broker owns both the thread
binding and the approval/recovery state outside the agent workspace.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Callable, Literal


_ACTIONS = frozenset({"disable", "enable", "stop_active"})
_REASON_CODE = "protected_host_broker_required"


class CodexAgentControlError(RuntimeError):
    """Raised when a Codex host mutation lacks protected host authority."""


class CodexAppServerClient:
    """Deprecated compatibility stub that always fails closed."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise CodexAgentControlError(
            "direct Codex App Server control is unavailable: "
            "protected host broker required"
        )


def _unavailable(*, operation: str, action: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "codex-agent-control-availability/1.0",
        "status": "unavailable",
        "operation": operation,
        "reason_code": _REASON_CODE,
        "reason": (
            "The tested Codex 0.147.0 stdio MCP contract has no documented or "
            "validated authoritative caller-task binding and proposal-bound "
            "user approval receipt"
        ),
        "authoritative_thread_identity_available": False,
        "protected_host_approval_receipt_available": False,
        "current_session_capability_revocation_available": False,
        "proposal_created": False,
        "canonical_changed": False,
        "host_changed": False,
        "root_thread_targeting_exposed": False,
        "separate_top_level_task_control_exposed": False,
        "required_boundary": "external_protected_host_broker",
        "fresh_session_candidate_settings": [
            "agents.enabled=false",
            "features.multi_agent=false",
            "features.multi_agent_v2=false",
        ],
        "spawn_rejection_verification_required": True,
    }
    if action is not None:
        result["requested_action"] = action
    return result


def codex_agent_status() -> dict[str, Any]:
    """Report why current-task descendant status is unavailable.

    This function intentionally does not read ``CODEX_THREAD_ID``, caller
    environment, Codex history, or an App Server endpoint.
    """

    return _unavailable(operation="status")


def prepare_codex_agent_control(
    root: str | Path,
    *,
    action: Literal["disable", "enable", "stop_active"] | str,
) -> dict[str, Any]:
    """Fail closed without creating a proposal or touching host state."""

    del root
    if action not in _ACTIONS:
        raise CodexAgentControlError("agent-control action is unsupported")
    return _unavailable(operation="prepare", action=action)


def apply_codex_agent_control(
    root: str | Path,
    *,
    proposal_hash: str,
    confirm_proposal_hash: str,
    codex_home: str | Path | None = None,
    client: object | None = None,
    feature_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Reject every direct apply attempt before any observable side effect.

    The retained Python symbol makes older integrations fail safely instead of
    importing a removed name and falling back to an unsafe local workaround.
    None of the arguments are inspected because caller-controlled values cannot
    prove user approval.
    """

    del (
        root,
        proposal_hash,
        confirm_proposal_hash,
        codex_home,
        client,
        feature_runner,
    )
    raise CodexAgentControlError(
        "direct Codex agent-control apply is unavailable: protected host broker required"
    )


__all__ = [
    "CodexAppServerClient",
    "CodexAgentControlError",
    "apply_codex_agent_control",
    "codex_agent_status",
    "prepare_codex_agent_control",
]
