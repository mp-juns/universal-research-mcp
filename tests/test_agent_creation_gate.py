from __future__ import annotations

from copy import deepcopy

import pytest

from universal_research_mcp.governance.agent_creation import (
    AGENT_CREATION_ISSUE,
    AgentCreationDisclosureError,
    agent_creation_disclosure_hash,
    normalize_agent_creation_disclosure,
    validate_agent_creation_packets,
)


def _disclosure(*, count: int = 2) -> dict:
    return {
        "schema_version": "agent-creation-disclosure/1.0",
        "reason": "The two evidence checks are independent and materially faster in parallel.",
        "delegated_tasks": [f"Review bounded evidence set {index}." for index in range(count)],
        "agent_count": count,
        "direct_execution_alternative": "The primary agent can perform both checks sequentially.",
        "expected_additional_tokens": {
            "minimum": 2_000,
            "likely": 8_000,
            "maximum": 20_000,
        },
        "expected_elapsed_minutes": {
            "minimum": 1,
            "likely": 4,
            "maximum": 15,
        },
        "scope": {
            "paths": ["docs/a.md", "docs/b.md"],
            "network": False,
            "model_execution": True,
            "writes": False,
        },
    }


def _packet(disclosure: dict, *, approval: bool = True, opt_in: bool = True) -> dict:
    return {
        "agent_creation_disclosure": deepcopy(disclosure),
        "scope": {
            "allowed_paths": deepcopy(disclosure["scope"]["paths"]),
            "allowed_actions": [],
            "allow_network": disclosure["scope"]["network"],
        },
        "authority": {
            "approval_refs": ["approval_agent_creation_01"] if approval else [],
            "user_opt_ins": ["agent_creation"] if opt_in else [],
        },
    }


def test_exact_disclosure_and_shared_user_approval_are_required() -> None:
    disclosure = _disclosure()
    packets = [_packet(disclosure), _packet(disclosure)]
    issues, normalized = validate_agent_creation_packets(
        packets,
        expected_agent_count=2,
    )
    assert issues == []
    assert normalized == disclosure
    assert agent_creation_disclosure_hash(normalized).startswith("sha256:")

    for mutation in (
        lambda packet: packet.pop("agent_creation_disclosure"),
        lambda packet: packet["authority"].update(approval_refs=[]),
        lambda packet: packet["authority"].update(user_opt_ins=[]),
    ):
        changed = deepcopy(packets)
        mutation(changed[0])
        changed_issues, _normalized = validate_agent_creation_packets(
            changed,
            expected_agent_count=2,
        )
        assert changed_issues
        assert {issue["code"] for issue in changed_issues} == {AGENT_CREATION_ISSUE}


def test_disclosure_change_or_count_mismatch_fails_closed() -> None:
    disclosure = _disclosure()
    changed = deepcopy(disclosure)
    changed["reason"] = "A different explanation that the user did not approve."
    issues, normalized = validate_agent_creation_packets(
        [_packet(disclosure), _packet(changed)],
        expected_agent_count=2,
    )
    assert normalized is None
    assert any("exact approved disclosure" in issue["message"] for issue in issues)

    with pytest.raises(AgentCreationDisclosureError, match="agent_count"):
        normalize_agent_creation_disclosure(disclosure, expected_agent_count=1)


def test_disclosure_rejects_unbounded_or_ambiguous_estimates() -> None:
    disclosure = _disclosure()
    disclosure["expected_additional_tokens"] = {
        "minimum": 10_000,
        "likely": 5_000,
        "maximum": 20_000,
    }
    with pytest.raises(AgentCreationDisclosureError, match="monotonic"):
        normalize_agent_creation_disclosure(disclosure)

    disclosure = _disclosure()
    disclosure["scope"]["model_execution"] = False
    with pytest.raises(AgentCreationDisclosureError, match="model execution"):
        normalize_agent_creation_disclosure(disclosure)


@pytest.mark.parametrize(
    ("mutate_packet", "message"),
    [
        (
            lambda packet: packet["scope"].update(allowed_paths=["private/**"]),
            "paths do not match",
        ),
        (
            lambda packet: packet["scope"].update(allow_network=True),
            "network scope does not match",
        ),
        (
            lambda packet: packet["scope"].update(
                allowed_actions=["edit_derived_artifact"],
            ),
            "write scope does not match",
        ),
    ],
)
def test_disclosure_scope_must_match_requested_packet_scope(
    mutate_packet,
    message: str,
) -> None:
    disclosure = _disclosure()
    packets = [_packet(disclosure), _packet(disclosure)]
    mutate_packet(packets[1])

    issues, normalized = validate_agent_creation_packets(
        packets,
        expected_agent_count=2,
    )

    assert normalized is None
    assert any(message in issue["message"] for issue in issues)
