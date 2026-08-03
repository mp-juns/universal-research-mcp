"""OpenAI REST adapter with an injected, explicitly approved transport."""

from __future__ import annotations

from typing import Any, Mapping

from .common import finite_vector, require_success, usage_from, validate_base_url
from .contracts import (
    Availability,
    Capability,
    EmbeddingRequest,
    EmbeddingResult,
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


class OpenAIProvider:
    provider_id = "openai"
    is_remote = True
    capabilities = frozenset({Capability.EMBEDDING, Capability.GENERATION})

    def __init__(
        self,
        *,
        transport: HttpTransport,
        credential_ref: str,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.transport = transport
        self.credential_ref = str(CredentialRef.parse(credential_ref))
        self.base_url = validate_base_url(base_url, frozenset({"api.openai.com"}))

    def preflight(self, capability: Capability) -> Availability:
        if capability not in self.capabilities:
            return Availability.unavailable("OpenAI adapter does not implement the requested capability")
        if not self.credential_ref:
            return Availability.unavailable("OpenAI credential reference is not configured")
        return Availability.ready()

    def invoke(
        self,
        request: ProviderRequest,
        credential: SecretLike | None = None,
    ) -> ProviderResult:
        if request.capability not in self.capabilities:
            raise ProviderRequestError("OpenAI adapter received an unsupported capability")
        if credential is None:
            raise ProviderRequestError("OpenAI invocation requires an opaque credential")
        secret = credential.reveal()
        headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
        if isinstance(request, EmbeddingRequest):
            payload: dict[str, Any] = {"model": request.model, "input": list(request.texts)}
            if request.dimensions is not None:
                payload["dimensions"] = request.dimensions
            path = "/embeddings"
        elif isinstance(request, GenerationRequest):
            messages = []
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            messages.extend({"role": message.role, "content": message.content} for message in request.messages)
            payload = {
                "model": request.model,
                "messages": messages,
                "max_tokens": request.max_output_tokens,
            }
            if request.temperature is not None:
                payload["temperature"] = request.temperature
            path = "/chat/completions"
        else:  # pragma: no cover - closed union guard
            raise ProviderRequestError("OpenAI adapter received an unknown request type")
        try:
            response = self.transport.request(
                method="POST",
                url=f"{self.base_url}{path}",
                headers=headers,
                json_body=payload,
                timeout_seconds=request.timeout_seconds,
            )
        except Exception as exc:
            safe = safe_exception(exc, (secret,))
            raise ProviderRequestError(
                "OpenAI transport failed",
                details={"provider_id": self.provider_id, "error": safe},
            ) from None
        require_success(self.provider_id, response.status_code, response.json_body, (secret,))
        if isinstance(request, EmbeddingRequest):
            return self._embedding_result(request, response.json_body)
        return self._generation_result(request, response.json_body)

    def _embedding_result(
        self,
        request: EmbeddingRequest,
        body: Mapping[str, Any],
    ) -> EmbeddingResult:
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(request.texts):
            raise ProviderRequestError("OpenAI embedding count does not match the request")
        indexed: list[tuple[int, tuple[float, ...]]] = []
        for position, item in enumerate(data):
            if not isinstance(item, Mapping):
                raise ProviderRequestError("OpenAI returned a malformed embedding item")
            raw_index = item.get("index", position)
            if not isinstance(raw_index, int):
                raise ProviderRequestError("OpenAI returned a malformed embedding index")
            indexed.append((raw_index, finite_vector(item.get("embedding"))))
        indexed.sort(key=lambda item: item[0])
        if [index for index, _ in indexed] != list(range(len(request.texts))):
            raise ProviderRequestError("OpenAI embedding indexes are incomplete or duplicated")
        vectors = tuple(vector for _, vector in indexed)
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or (request.dimensions is not None and dimensions != {request.dimensions}):
            raise ProviderRequestError("OpenAI embedding dimensions do not match the request")
        return EmbeddingResult(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model=str(body.get("model") or request.model),
            vectors=vectors,
            usage=usage_from(body),
        )

    def _generation_result(
        self,
        request: GenerationRequest,
        body: Mapping[str, Any],
    ) -> GenerationResult:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ProviderRequestError("OpenAI returned no generation choice")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ProviderRequestError("OpenAI returned malformed generation content")
        return GenerationResult(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model=str(body.get("model") or request.model),
            text=content,
            usage=usage_from(body),
        )
