"""Injectable local provider used by the local-first router."""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import (
    Availability,
    Capability,
    CapabilityUnavailable,
    LocalHandler,
    ProviderRequest,
    ProviderResult,
    SecretLike,
)


@dataclass
class LocalProvider:
    handlers: dict[Capability, LocalHandler]
    availability: dict[Capability, Availability] = field(default_factory=dict)
    provider_id: str = "local"
    is_remote: bool = field(default=False, init=False)
    credential_ref: str | None = field(default=None, init=False)

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(self.handlers)

    def preflight(self, capability: Capability) -> Availability:
        if capability not in self.handlers:
            return Availability.unavailable("local provider does not implement the requested capability")
        return self.availability.get(capability, Availability.ready())

    def invoke(
        self,
        request: ProviderRequest,
        credential: SecretLike | None = None,
    ) -> ProviderResult:
        readiness = self.preflight(request.capability)
        if not readiness.available:
            raise CapabilityUnavailable(readiness.reason)
        return self.handlers[request.capability](request)
