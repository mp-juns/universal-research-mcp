"""Strict OpenAI-compatible provider for an explicitly approved loopback server.

The transport deliberately accepts only the two literal loopback origins used
by the project: ``127.0.0.1`` and ``[::1]`` with an explicit port.  It uses
``http.client`` directly, so environment proxy settings and redirect handlers
are never involved.  Each request is one bounded, non-streaming JSON exchange
with no retry or fallback behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection
import json
from math import isfinite
from typing import Any, Callable, Mapping
from urllib.parse import SplitResult, urlsplit

from .common import finite_vector, require_success, usage_from
from .contracts import (
    Availability,
    Capability,
    EmbeddingRequest,
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    ProviderConfigurationError,
    ProviderRequest,
    ProviderRequestError,
    ProviderResult,
    SecretLike,
)
from .credentials import CredentialRef
from .http import (
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_MAX_RESPONSE_BYTES,
    HARD_MAX_BODY_BYTES,
    HttpResponse,
    HttpTransport,
    HttpTransportError,
)
from .redaction import safe_exception


LOOPBACK_PROVIDER_ID = "openai-compatible-loopback"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_BASE_PATH = "/v1"
_REQUEST_PATHS = frozenset({"/v1/chat/completions", "/v1/embeddings"})
_FORBIDDEN_REQUEST_HEADERS = frozenset(
    {
        "connection",
        "host",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "transfer-encoding",
        "upgrade",
    }
)
MAX_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class _LoopbackTarget:
    host: str
    port: int
    path: str


def _parse_loopback_url(value: str, *, allowed_paths: frozenset[str]) -> _LoopbackTarget:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("loopback URL is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("loopback URL contains control characters")
    try:
        parsed: SplitResult = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("loopback URL has an invalid port") from exc
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("loopback URL must use a literal loopback HTTP origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("loopback URL must not contain credentials")
    if parsed.query or parsed.fragment or parsed.path not in allowed_paths:
        raise ValueError("loopback URL has an unapproved path or URL component")
    if port is None or port < 1 or port > 65_535:
        raise ValueError("loopback URL requires an explicit valid port")
    authority = f"127.0.0.1:{port}" if parsed.hostname == "127.0.0.1" else f"[::1]:{port}"
    canonical = f"http://{authority}{parsed.path}"
    if value != canonical:
        # Exact canonical comparison rejects DNS names, alternate IP spellings,
        # encoded components, empty query/fragment suffixes, and port ambiguity.
        raise ValueError("loopback URL is not in canonical form")
    return _LoopbackTarget(parsed.hostname, port, parsed.path)


def validate_loopback_endpoint(endpoint: str) -> str:
    """Return one canonical, fixed ``/v1`` loopback endpoint or fail closed."""

    try:
        _parse_loopback_url(endpoint, allowed_paths=frozenset({_BASE_PATH}))
    except ValueError:
        raise ProviderConfigurationError(
            "loopback endpoint must be exactly http://127.0.0.1:PORT/v1 "
            "or http://[::1]:PORT/v1"
        ) from None
    return endpoint


ConnectionFactory = Callable[..., Any]


class LoopbackJsonTransport:
    """Perform exactly one bounded JSON POST to a literal loopback address."""

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory | None = None,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._connection_factory = connection_factory or HTTPConnection
        self.max_request_bytes = _bounded_limit("max_request_bytes", max_request_bytes)
        self.max_response_bytes = _bounded_limit("max_response_bytes", max_response_bytes)

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> HttpResponse:
        if str(method).upper() != "POST":
            raise HttpTransportError("loopback transport supports POST requests only")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > MAX_TIMEOUT_SECONDS
        ):
            raise HttpTransportError(
                f"loopback timeout must be positive, finite, and at most {int(MAX_TIMEOUT_SECONDS)} seconds"
            )
        try:
            target = _parse_loopback_url(url, allowed_paths=_REQUEST_PATHS)
        except ValueError:
            raise HttpTransportError("loopback request target is not approved") from None

        normalized_headers: dict[str, str] = {}
        for key, value in headers.items():
            name = str(key)
            rendered_value = str(value)
            folded_name = name.casefold()
            if "\r" in name or "\n" in name or "\r" in rendered_value or "\n" in rendered_value:
                raise HttpTransportError("loopback request contains an invalid header")
            if folded_name in _FORBIDDEN_REQUEST_HEADERS:
                raise HttpTransportError("loopback request contains a forbidden transport header")
            if folded_name in {"accept", "content-type"}:
                continue
            normalized_headers[name] = rendered_value
        normalized_headers["Accept"] = "application/json"
        normalized_headers["Content-Type"] = "application/json"
        try:
            payload = json.dumps(
                dict(json_body), ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise HttpTransportError("loopback request body is not JSON serializable") from exc
        if len(payload) > self.max_request_bytes:
            raise HttpTransportError("loopback request JSON exceeds the configured byte limit")

        connection: Any | None = None
        response: Any | None = None
        try:
            # Only a validated numeric loopback literal reaches this direct
            # connection.  No proxy handler, redirect handler, or URL opener is
            # consulted.
            connection = self._connection_factory(
                target.host, target.port, timeout=float(timeout_seconds),
            )
            connection.request(
                "POST", target.path, body=payload, headers=normalized_headers,
            )
            response = connection.getresponse()
            status_code = _status_code(response)
            response_headers = _response_headers(response)
            if 300 <= status_code < 400:
                raise HttpTransportError("loopback redirects are forbidden")
            content_type = response_headers.get("content-type", "").casefold()
            if content_type.split(";", 1)[0].strip() == "text/event-stream":
                raise HttpTransportError("loopback streaming responses are forbidden")
            body = _read_bounded(response, self.max_response_bytes, response_headers)
        except HttpTransportError:
            raise
        except Exception:
            raise HttpTransportError("loopback HTTP request failed") from None
        finally:
            for resource in (response, connection):
                close = getattr(resource, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpTransportError("loopback response is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, Mapping):
            raise HttpTransportError("loopback response JSON root must be an object")
        return HttpResponse(status_code, dict(decoded), response_headers)


class OpenAICompatibleLoopbackProvider:
    """OpenAI-compatible adapter confined to an approved loopback endpoint.

    ``is_remote`` remains true intentionally: the existing router therefore
    requires explicit opt-in and a bounded policy even though monetary cost can
    be zero and the network scope is loopback-only.
    """

    provider_id = LOOPBACK_PROVIDER_ID
    is_remote = True
    network_scope = "loopback"
    capabilities = frozenset({Capability.EMBEDDING, Capability.GENERATION})

    def __init__(
        self,
        *,
        transport: HttpTransport,
        endpoint: str,
        credential_ref: str | None = None,
    ) -> None:
        self.transport = transport
        self.endpoint = validate_loopback_endpoint(endpoint)
        self.credential_ref = (
            None if credential_ref is None else str(CredentialRef.parse(credential_ref))
        )

    def preflight(self, capability: Capability) -> Availability:
        if capability not in self.capabilities:
            return Availability.unavailable(
                "loopback OpenAI-compatible adapter does not implement the requested capability"
            )
        return Availability.ready()

    def invoke(
        self,
        request: ProviderRequest,
        credential: SecretLike | None = None,
    ) -> ProviderResult:
        if request.capability not in self.capabilities:
            raise ProviderRequestError(
                "loopback OpenAI-compatible adapter received an unsupported capability"
            )
        if self.credential_ref is not None and credential is None:
            raise ProviderRequestError(
                "configured loopback credential was not supplied at the invocation boundary"
            )
        if self.credential_ref is None and credential is not None:
            raise ProviderRequestError(
                "loopback invocation supplied an unconfigured credential"
            )

        secret = credential.reveal() if credential is not None else ""
        secrets = (secret,) if secret else ()
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        if isinstance(request, EmbeddingRequest):
            payload: dict[str, Any] = {
                "model": request.model,
                "input": list(request.texts),
            }
            if request.dimensions is not None:
                payload["dimensions"] = request.dimensions
            path = "/embeddings"
        elif isinstance(request, GenerationRequest):
            messages = []
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            messages.extend(
                {"role": message.role, "content": message.content}
                for message in request.messages
            )
            payload = {
                "model": request.model,
                "messages": messages,
                "max_tokens": request.max_output_tokens,
                "stream": False,
            }
            if request.temperature is not None:
                payload["temperature"] = request.temperature
            path = "/chat/completions"
        else:  # pragma: no cover - closed request union guard
            raise ProviderRequestError(
                "loopback OpenAI-compatible adapter received an unknown request type"
            )
        try:
            response = self.transport.request(
                method="POST",
                url=f"{self.endpoint}{path}",
                headers=headers,
                json_body=payload,
                timeout_seconds=request.timeout_seconds,
            )
        except Exception as exc:
            safe = safe_exception(exc, secrets)
            raise ProviderRequestError(
                "loopback OpenAI-compatible transport failed",
                details={"provider_id": self.provider_id, "error": safe},
            ) from None
        require_success(self.provider_id, response.status_code, response.json_body, secrets)
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
            raise ProviderRequestError(
                "loopback embedding count does not match the request"
            )
        indexed: list[tuple[int, tuple[float, ...]]] = []
        for position, item in enumerate(data):
            if not isinstance(item, Mapping):
                raise ProviderRequestError("loopback returned a malformed embedding item")
            raw_index = item.get("index", position)
            if isinstance(raw_index, bool) or not isinstance(raw_index, int):
                raise ProviderRequestError("loopback returned a malformed embedding index")
            indexed.append((raw_index, finite_vector(item.get("embedding"))))
        indexed.sort(key=lambda item: item[0])
        if [index for index, _ in indexed] != list(range(len(request.texts))):
            raise ProviderRequestError(
                "loopback embedding indexes are incomplete or duplicated"
            )
        vectors = tuple(vector for _, vector in indexed)
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or (
            request.dimensions is not None and dimensions != {request.dimensions}
        ):
            raise ProviderRequestError(
                "loopback embedding dimensions do not match the request"
            )
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
            raise ProviderRequestError("loopback returned no generation choice")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ProviderRequestError("loopback returned malformed generation content")
        return GenerationResult(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model=str(body.get("model") or request.model),
            text=content,
            usage=usage_from(body),
        )


def _bounded_limit(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 1 or value > HARD_MAX_BODY_BYTES:
        raise ValueError(f"{name} must be in [1, {HARD_MAX_BODY_BYTES}]")
    return value


def _status_code(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        getter = getattr(response, "getcode", None)
        value = getter() if callable(getter) else None
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise HttpTransportError("loopback response has no valid HTTP status")
    return value


def _response_headers(response: Any) -> dict[str, str]:
    getter = getattr(response, "getheaders", None)
    if callable(getter):
        values = getter()
    else:
        raw = getattr(response, "headers", None)
        items = getattr(raw, "items", None)
        values = items() if callable(items) else ()
    return {str(key).casefold(): str(value) for key, value in values}


def _read_bounded(
    response: Any,
    maximum: int,
    headers: Mapping[str, str],
) -> bytes:
    content_length = headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except (TypeError, ValueError):
            declared = -1
        if declared > maximum:
            raise HttpTransportError(
                "loopback response JSON exceeds the configured byte limit"
            )
    body = response.read(maximum + 1)
    if not isinstance(body, bytes):
        raise HttpTransportError("loopback response body must be bytes")
    if len(body) > maximum:
        raise HttpTransportError(
            "loopback response JSON exceeds the configured byte limit"
        )
    return body


__all__ = [
    "LOOPBACK_PROVIDER_ID",
    "LoopbackJsonTransport",
    "OpenAICompatibleLoopbackProvider",
    "validate_loopback_endpoint",
]
