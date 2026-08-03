"""Provider-neutral contracts for explicitly authorized inference.

These types deliberately keep credential values outside serializable provider
profiles and request records.  A provider adapter receives a short-lived
``SecretValue`` only at the final invocation boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any, Callable, Mapping, Protocol, TypeAlias


class Capability(str, Enum):
    EMBEDDING = "embedding"
    GENERATION = "generation"


class ProviderError(RuntimeError):
    """Base class for sanitized provider-boundary failures."""

    code = "provider_error"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class ProviderConfigurationError(ProviderError):
    code = "provider_configuration_error"


class CapabilityUnavailable(ProviderError):
    code = "capability_unavailable"


class RemoteOptInRequired(ProviderError):
    code = "remote_opt_in_required"


class BudgetExceeded(ProviderError):
    code = "provider_budget_exceeded"


class ProviderRequestError(ProviderError):
    code = "provider_request_failed"


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str

    @classmethod
    def ready(cls) -> "Availability":
        return cls(True, "available")

    @classmethod
    def unavailable(cls, reason: str) -> "Availability":
        return cls(False, reason)


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise ValueError("message role must be user or assistant")
        if not self.content:
            raise ValueError("message content must not be empty")


@dataclass(frozen=True)
class EmbeddingRequest:
    request_id: str
    model: str
    texts: tuple[str, ...]
    dimensions: int | None = None
    estimated_input_tokens: int = 0
    estimated_cost_micros: int = 0
    timeout_seconds: float = 60.0
    capability: Capability = field(default=Capability.EMBEDDING, init=False)

    def __post_init__(self) -> None:
        if not self.request_id or not self.model:
            raise ValueError("request_id and model are required")
        if not self.texts or any(not isinstance(text, str) or not text for text in self.texts):
            raise ValueError("embedding texts must contain non-empty strings")
        if self.dimensions is not None and self.dimensions < 1:
            raise ValueError("dimensions must be positive when supplied")
        _validate_estimates(self.estimated_input_tokens, 0, self.estimated_cost_micros)
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")


@dataclass(frozen=True)
class GenerationRequest:
    request_id: str
    model: str
    messages: tuple[Message, ...]
    max_output_tokens: int
    system_prompt: str | None = None
    temperature: float | None = None
    estimated_input_tokens: int = 0
    estimated_cost_micros: int = 0
    timeout_seconds: float = 60.0
    capability: Capability = field(default=Capability.GENERATION, init=False)

    def __post_init__(self) -> None:
        if not self.request_id or not self.model:
            raise ValueError("request_id and model are required")
        if not self.messages:
            raise ValueError("at least one generation message is required")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.temperature is not None and (not isfinite(self.temperature) or self.temperature < 0):
            raise ValueError("temperature must be non-negative and finite")
        _validate_estimates(
            self.estimated_input_tokens,
            self.max_output_tokens,
            self.estimated_cost_micros,
        )
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")


ProviderRequest: TypeAlias = EmbeddingRequest | GenerationRequest


def _validate_estimates(input_tokens: int, output_tokens: int, cost_micros: int) -> None:
    if input_tokens < 0 or output_tokens < 0 or cost_micros < 0:
        raise ValueError("token and cost estimates must be non-negative")


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("usage counts must be non-negative")


@dataclass(frozen=True)
class EmbeddingResult:
    request_id: str
    provider_id: str
    model: str
    vectors: tuple[tuple[float, ...], ...]
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    capability: Capability = field(default=Capability.EMBEDDING, init=False)


@dataclass(frozen=True)
class GenerationResult:
    request_id: str
    provider_id: str
    model: str
    text: str
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    capability: Capability = field(default=Capability.GENERATION, init=False)


ProviderResult: TypeAlias = EmbeddingResult | GenerationResult


@dataclass(frozen=True)
class RemoteBudget:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_estimated_cost_micros: int

    def __post_init__(self) -> None:
        if min(
            self.max_calls,
            self.max_input_tokens,
            self.max_output_tokens,
            self.max_estimated_cost_micros,
        ) < 0:
            raise ValueError("remote budget limits must be non-negative")

    def validate(self, request: ProviderRequest) -> None:
        output_tokens = request.max_output_tokens if isinstance(request, GenerationRequest) else 0
        checks = (
            (1, self.max_calls, "calls"),
            (request.estimated_input_tokens, self.max_input_tokens, "input tokens"),
            (output_tokens, self.max_output_tokens, "output tokens"),
            (request.estimated_cost_micros, self.max_estimated_cost_micros, "estimated cost"),
        )
        exceeded = [name for value, limit, name in checks if value > limit]
        if exceeded:
            raise BudgetExceeded(
                "remote request exceeds approved budget",
                details={"exceeded": exceeded},
            )


@dataclass(frozen=True)
class RemotePolicy:
    """Explicit authorization envelope for one remote request.

    ``approved`` must be true and the selected provider must appear in
    ``allowed_provider_ids``.  This object intentionally contains no secret.
    """

    approved: bool = False
    allowed_provider_ids: frozenset[str] = field(default_factory=frozenset)
    budget: RemoteBudget | None = None


class SecretLike(Protocol):
    def reveal(self) -> str: ...


class ProviderAdapter(Protocol):
    provider_id: str
    is_remote: bool
    capabilities: frozenset[Capability]
    credential_ref: str | None

    def preflight(self, capability: Capability) -> Availability: ...

    def invoke(
        self,
        request: ProviderRequest,
        credential: SecretLike | None = None,
    ) -> ProviderResult: ...


LocalHandler: TypeAlias = Callable[[ProviderRequest], ProviderResult]
