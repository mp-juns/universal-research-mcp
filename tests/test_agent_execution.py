from __future__ import annotations

from pathlib import Path

import pytest

from universal_research_mcp.agent_execution import build_generation_executor
from universal_research_mcp.runtime.provider_config import (
    configure_loopback_generation,
    configure_remote_provider,
)


def _build(root: Path, **overrides):
    values = {
        "route": "loopback",
        "max_calls": 2,
        "max_input_tokens": 10_000,
        "max_output_tokens": 1_000,
        "max_output_tokens_per_agent": 500,
        "max_cost_usd": "0",
        "input_cost_per_million_tokens_usd": "0",
        "output_cost_per_million_tokens_usd": "0",
        "request_timeout_seconds": 30,
    }
    values.update(overrides)
    return build_generation_executor(root, **values)


def test_loopback_executor_build_is_zero_cost_explicit_and_does_not_call_server(
    tmp_path: Path,
) -> None:
    configure_loopback_generation(
        tmp_path,
        endpoint="http://127.0.0.1:11434/v1",
        model="local-fixture",
    )
    bundle = _build(tmp_path)
    assert bundle.provider_id == "openai-compatible-loopback"
    assert bundle.network_scope == "loopback"
    assert bundle.executor.usage_snapshot()["provider_calls_reserved"] == 0

    with pytest.raises(ValueError, match="explicitly zero"):
        _build(tmp_path, input_cost_per_million_tokens_usd="1")


def test_remote_executor_requires_current_positive_prices_and_cost_ceiling(
    tmp_path: Path,
) -> None:
    configure_remote_provider(
        tmp_path,
        capability="generation",
        provider_id="openai",
        model="remote-fixture",
        credential_ref="env:OPENAI_API_KEY",
    )
    with pytest.raises(ValueError, match="positive and finite"):
        _build(
            tmp_path,
            route="remote",
            max_cost_usd="1",
            input_cost_per_million_tokens_usd="0",
            output_cost_per_million_tokens_usd="1",
        )
    with pytest.raises(ValueError, match="positive cost ceiling"):
        _build(
            tmp_path,
            route="remote",
            max_cost_usd="0",
            input_cost_per_million_tokens_usd="1",
            output_cost_per_million_tokens_usd="1",
        )


def test_no_configured_route_is_never_replaced_by_an_automatic_fallback(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="no loopback"):
        _build(tmp_path)
    configure_loopback_generation(
        tmp_path,
        endpoint="http://127.0.0.1:11434/v1",
        model="local-fixture",
    )
    with pytest.raises(ValueError, match="no remote"):
        _build(
            tmp_path,
            route="remote",
            max_cost_usd="1",
            input_cost_per_million_tokens_usd="1",
            output_cost_per_million_tokens_usd="1",
        )
