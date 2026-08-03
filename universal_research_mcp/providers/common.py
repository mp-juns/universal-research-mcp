"""Shared validation helpers for remote REST adapters."""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .contracts import ProviderConfigurationError, ProviderRequestError, ProviderUsage
from .redaction import sanitize


def validate_base_url(base_url: str, allowed_hosts: frozenset[str]) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in allowed_hosts:
        raise ProviderConfigurationError("provider endpoint must use an approved HTTPS origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderConfigurationError("provider endpoint must not contain credentials or query data")
    return base_url.rstrip("/")


def require_success(provider_id: str, status_code: int, body: Mapping[str, Any], secrets: Sequence[str]) -> None:
    if 200 <= status_code < 300:
        return
    safe_body = sanitize(body, secrets)
    raise ProviderRequestError(
        f"{provider_id} request failed with HTTP {status_code}",
        details={"provider_id": provider_id, "status_code": status_code, "body": safe_body},
    )


def usage_from(body: Mapping[str, Any]) -> ProviderUsage:
    raw = body.get("usage")
    if not isinstance(raw, Mapping):
        return ProviderUsage()
    input_tokens = _nonnegative_int(raw.get("prompt_tokens", raw.get("input_tokens", 0)))
    output_tokens = _nonnegative_int(raw.get("completion_tokens", raw.get("output_tokens", 0)))
    total_tokens = _nonnegative_int(raw.get("total_tokens", input_tokens + output_tokens))
    return ProviderUsage(input_tokens, output_tokens, total_tokens)


def finite_vector(value: Any) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise ProviderRequestError("provider returned an empty or malformed embedding vector")
    try:
        vector = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ProviderRequestError("provider returned a non-numeric embedding vector") from exc
    if not all(isfinite(item) for item in vector):
        raise ProviderRequestError("provider returned a non-finite embedding vector")
    return vector


def _nonnegative_int(value: Any) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, normalized)
