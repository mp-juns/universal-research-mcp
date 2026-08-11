"""Anthropic generation-only REST adapter with injected transport."""

from __future__ import annotations

from typing import Any, Mapping

from .common import require_success, usage_from, validate_base_url
from .contracts import (
    Availability,
    Capability,
    CapabilityUnavailable,
    GenerationRequest,
    GenerationResult,
    ProviderRequest,
    ProviderRequestError,
    ProviderResult,
    SecretLike,
)
from .http import HttpTransport
from .credentials import CredentialRef
from .redaction import safe_exception


class AnthropicProvider:
    provider_id = "anthropic"
    is_remote = True
    capabilities = frozenset({Capability.GENERATION})

    def __init__(
        self,
        *,
        transport: HttpTransport,
        credential_ref: str,
        base_url: str = "https://api.anthropic.com/v1",
        api_version: str = "2023-06-01",
    ) -> None:
        self.transport = transport
        self.credential_ref = str(CredentialRef.parse(credential_ref))
        self.base_url = validate_base_url(base_url, frozenset({"api.anthropic.com"}))
        self.api_version = api_version

    def preflight(self, capability: Capability) -> Availability:
        if capability is Capability.EMBEDDING:
            return Availability.unavailable("Anthropic adapter is generation-only")
        if capability not in self.capabilities:
            return Availability.unavailable("Anthropic adapter does not implement the requested capability")
        if not self.credential_ref:
            return Availability.unavailable("Anthropic credential reference is not configured")
        return Availability.ready()

    def invoke(
        self,
        request: ProviderRequest,
        credential: SecretLike | None = None,
    ) -> ProviderResult:
        if not isinstance(request, GenerationRequest):
            raise CapabilityUnavailable("Anthropic adapter is generation-only")
        if credential is None:
            raise ProviderRequestError("Anthropic invocation requires an opaque credential")
        secret = credential.reveal()
        headers = {
            "x-api-key": secret,
            "anthropic-version": self.api_version,
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "max_tokens": request.max_output_tokens,
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        try:
            response = self.transport.request(
                method="POST",
                url=f"{self.base_url}/messages",
                headers=headers,
                json_body=payload,
                timeout_seconds=request.timeout_seconds,
            )
        except Exception as exc:
            safe = safe_exception(exc, (secret,))
            raise ProviderRequestError(
                "Anthropic transport failed",
                details={"provider_id": self.provider_id, "error": safe},
            ) from None
        require_success(self.provider_id, response.status_code, response.json_body, (secret,))
        return self._generation_result(request, response.json_body)

    def _generation_result(
        self,
        request: GenerationRequest,
        body: Mapping[str, Any],
    ) -> GenerationResult:
        content = body.get("content")
        if not isinstance(content, list):
            raise ProviderRequestError("Anthropic returned malformed generation content")
        texts = [
            str(item["text"])
            for item in content
            if isinstance(item, Mapping) and item.get("type") == "text" and isinstance(item.get("text"), str)
        ]
        if not texts:
            raise ProviderRequestError("Anthropic returned no text generation content")
        return GenerationResult(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model=str(body.get("model") or request.model),
            text="\n".join(texts),
            usage=usage_from(body),
        )
