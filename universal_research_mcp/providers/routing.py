"""Local-first provider selection with explicit, bounded remote opt-in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import (
    Availability,
    CapabilityUnavailable,
    ProviderAdapter,
    ProviderRequest,
    ProviderResult,
    RemoteOptInRequired,
    RemotePolicy,
)
from .credentials import CredentialRef, CredentialResolver


def provider_status(provider: ProviderAdapter) -> dict[str, Any]:
    """Return provider metadata without exposing a credential locator or value."""

    credential_kind: str | None = None
    if provider.credential_ref:
        credential_kind = CredentialRef.parse(provider.credential_ref).kind
    return {
        "provider_id": provider.provider_id,
        "remote": provider.is_remote,
        "capabilities": sorted(capability.value for capability in provider.capabilities),
        "credential": {
            "configured": bool(provider.credential_ref),
            "kind": credential_kind,
            "value": "[REDACTED]" if provider.credential_ref else None,
        },
    }


@dataclass(frozen=True)
class RoutedResult:
    provider_id: str
    remote: bool
    local_preflight: Availability
    result: ProviderResult


class ProviderRouter:
    """Select one provider and invoke it at most once.

    Provider failures are terminal for this call.  In particular, an ambiguous
    remote timeout is never followed by a request to a second provider.
    """

    def __init__(
        self,
        *,
        local: ProviderAdapter | None,
        remotes: Iterable[ProviderAdapter] = (),
        credentials: CredentialResolver | None = None,
    ) -> None:
        self.local = local
        self.remotes = tuple(remotes)
        self.credentials = credentials or CredentialResolver()

    def provider_status(self) -> dict[str, Any]:
        """Return a redacted snapshot suitable for diagnostics and user chat."""

        return {
            "local": provider_status(self.local) if self.local is not None else None,
            "remotes": [provider_status(provider) for provider in self.remotes],
        }

    def preflight(
        self,
        request: ProviderRequest,
        *,
        remote_policy: RemotePolicy | None = None,
    ) -> dict[str, Any]:
        """Plan one route without resolving secrets or issuing a request."""

        local_status = self._local_preflight(request)
        base: dict[str, Any] = {
            "capability": request.capability.value,
            "local": {"available": local_status.available, "reason": local_status.reason},
        }
        if self.local is not None and local_status.available:
            return {**base, "executable": True, "route": "local", "provider_id": self.local.provider_id}
        policy = remote_policy or RemotePolicy()
        if not policy.approved:
            return {**base, "executable": False, "route": "blocked", "reason": "remote_opt_in_required"}
        if policy.budget is None:
            return {**base, "executable": False, "route": "blocked", "reason": "remote_budget_required"}
        try:
            policy.budget.validate(request)
            selected = self._select_remote(request, policy)
        except Exception as exc:
            code = getattr(exc, "code", "capability_unavailable")
            return {**base, "executable": False, "route": "blocked", "reason": code}
        return {**base, "executable": True, "route": "remote", "provider_id": selected.provider_id}

    def execute(
        self,
        request: ProviderRequest,
        *,
        remote_policy: RemotePolicy | None = None,
    ) -> RoutedResult:
        local_status = self._local_preflight(request)
        if self.local is not None and local_status.available:
            # Invocation failure is terminal.  Do not convert it into a remote
            # fallback, because work may already have executed.
            result = self.local.invoke(request, None)
            return RoutedResult(self.local.provider_id, False, local_status, result)

        policy = remote_policy or RemotePolicy()
        if not policy.approved:
            raise RemoteOptInRequired(
                "local capability is unavailable and remote execution is not approved",
                details={"local_reason": local_status.reason},
            )
        if policy.budget is None:
            raise RemoteOptInRequired("remote execution requires an explicit budget")
        policy.budget.validate(request)

        selected = self._select_remote(request, policy)
        credential = (
            self.credentials.resolve(selected.credential_ref)
            if selected.credential_ref is not None
            else None
        )
        # One selected provider, one invocation, no automatic cross-provider
        # retry after a request is issued.
        result = selected.invoke(request, credential)
        return RoutedResult(selected.provider_id, True, local_status, result)

    def _local_preflight(self, request: ProviderRequest) -> Availability:
        if self.local is None:
            return Availability.unavailable("no local provider is configured")
        return self.local.preflight(request.capability)

    def _select_remote(
        self,
        request: ProviderRequest,
        policy: RemotePolicy,
    ) -> ProviderAdapter:
        for provider in self.remotes:
            if provider.provider_id not in policy.allowed_provider_ids:
                continue
            readiness = provider.preflight(request.capability)
            if readiness.available:
                return provider
        raise CapabilityUnavailable(
            "no approved remote provider can satisfy the requested capability",
            details={"allowed_provider_ids": sorted(policy.allowed_provider_ids)},
        )
