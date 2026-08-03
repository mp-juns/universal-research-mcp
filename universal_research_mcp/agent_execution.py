"""Construct one explicitly selected generation route without invoking it."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any

from governance.hashing import artifact_hash
from universal_research_mcp.harness import ProviderAgentExecutor
from universal_research_mcp.providers import (
    AnthropicProvider,
    CredentialResolver,
    LOOPBACK_PROVIDER_ID,
    LoopbackJsonTransport,
    OpenAICompatibleLoopbackProvider,
    OpenAIProvider,
    ProviderRouter,
    RemoteBudget,
    RemotePolicy,
    UrllibTransport,
)
from universal_research_mcp.runtime.provider_config import load_provider_config


@dataclass(frozen=True)
class GenerationExecutorBundle:
    """Uninvoked executor plus secret-free route metadata for runtime binding."""

    executor: ProviderAgentExecutor
    provider_id: str
    model: str
    route: str
    network_scope: str
    provider_configuration_hash: str

    def summary(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "route": self.route,
            "network_scope": self.network_scope,
            "provider_configuration_hash": self.provider_configuration_hash,
            "credential_values_exposed": False,
            "automatic_fallback": False,
            "hidden_retries": 0,
        }


def _decimal(value: str | float, *, label: str, strictly_positive: bool) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not parsed.is_finite() or parsed < 0 or (strictly_positive and parsed <= 0):
        comparator = "positive" if strictly_positive else "non-negative"
        raise ValueError(f"{label} must be {comparator} and finite")
    return parsed


def _cost_micros(value: str | float) -> int:
    amount = _decimal(value, label="maximum cost", strictly_positive=False)
    return int((amount * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))


def build_generation_executor(
    root: str | Path,
    *,
    route: str,
    max_calls: int,
    max_input_tokens: int,
    max_output_tokens: int,
    max_output_tokens_per_agent: int,
    max_cost_usd: str | float,
    input_cost_per_million_tokens_usd: str | float,
    output_cost_per_million_tokens_usd: str | float,
    request_timeout_seconds: float = 60.0,
) -> GenerationExecutorBundle:
    """Build, but do not authorize or call, one generation executor.

    The route is never selected by availability and is never changed after a
    failure. Loopback is deliberately subject to the same explicit execution,
    call, token, and timeout envelope as an internet provider. Callers must bind
    this executor to an exact validated ``AgentRuntime`` plan; constructing it
    is not user approval.
    """

    for name, value in (
        ("max_calls", max_calls),
        ("max_input_tokens", max_input_tokens),
        ("max_output_tokens", max_output_tokens),
        ("max_output_tokens_per_agent", max_output_tokens_per_agent),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if max_output_tokens_per_agent > max_output_tokens:
        raise ValueError("per-agent output ceiling exceeds the aggregate output budget")

    config = load_provider_config(root)
    if route == "loopback":
        selected = config["generation"].get("loopback")
        if not selected:
            raise ValueError("no loopback generation provider is configured")
        input_price = _decimal(
            input_cost_per_million_tokens_usd,
            label="loopback input token price",
            strictly_positive=False,
        )
        output_price = _decimal(
            output_cost_per_million_tokens_usd,
            label="loopback output token price",
            strictly_positive=False,
        )
        if input_price != 0 or output_price != 0:
            raise ValueError("loopback API token prices must be explicitly zero")
        provider = OpenAICompatibleLoopbackProvider(
            transport=LoopbackJsonTransport(),
            endpoint=selected["endpoint"],
            credential_ref=selected["credential_ref"],
        )
        provider_id = LOOPBACK_PROVIDER_ID
        network_scope = "loopback"
    elif route == "remote":
        selected = config["generation"].get("remote")
        if not selected:
            raise ValueError("no remote generation provider is configured")
        input_price = _decimal(
            input_cost_per_million_tokens_usd,
            label="remote input token price",
            strictly_positive=True,
        )
        output_price = _decimal(
            output_cost_per_million_tokens_usd,
            label="remote output token price",
            strictly_positive=True,
        )
        transport = UrllibTransport()
        if selected["provider_id"] == "openai":
            provider = OpenAIProvider(
                transport=transport,
                credential_ref=selected["credential_ref"],
            )
        elif selected["provider_id"] == "anthropic":
            provider = AnthropicProvider(
                transport=transport,
                credential_ref=selected["credential_ref"],
            )
        else:  # pragma: no cover - provider-config validation closes this union
            raise ValueError("unsupported remote generation provider")
        provider_id = selected["provider_id"]
        network_scope = "remote"
    else:
        raise ValueError("route must be exactly loopback or remote")

    maximum_cost_micros = _cost_micros(max_cost_usd)
    if route == "remote" and maximum_cost_micros < 1:
        raise ValueError("remote generation requires a positive cost ceiling")
    budget = RemoteBudget(
        max_calls=max_calls,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_estimated_cost_micros=maximum_cost_micros,
    )
    policy = RemotePolicy(
        approved=True,
        allowed_provider_ids=frozenset({provider_id}),
        budget=budget,
    )
    executor = ProviderAgentExecutor(
        router=ProviderRouter(
            local=None,
            remotes=(provider,),
            credentials=CredentialResolver(),
        ),
        remote_policy=policy,
        model=selected["model"],
        max_output_tokens=max_output_tokens_per_agent,
        input_cost_per_million_tokens_usd=str(input_price),
        output_cost_per_million_tokens_usd=str(output_price),
        request_timeout_seconds=request_timeout_seconds,
    )
    provider_configuration_hash = artifact_hash(selected)
    # AgentRuntime checks these immutable route attributes against its exact
    # run plan before it permits any dispatch. They contain no secret value.
    executor.provider_id = provider_id
    executor.network_scope = network_scope
    executor.provider_configuration_hash = provider_configuration_hash
    return GenerationExecutorBundle(
        executor=executor,
        provider_id=provider_id,
        model=selected["model"],
        route=route,
        network_scope=network_scope,
        provider_configuration_hash=provider_configuration_hash,
    )


__all__ = ["GenerationExecutorBundle", "build_generation_executor"]
