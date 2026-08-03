"""Narrow, injectable HTTPS JSON transport.

Constructing :class:`UrllibTransport` never opens a connection.  A caller must
still pass it through the provider execution boundary, where remote opt-in and
budget checks happen.  Each ``request`` performs exactly one opener call: this
layer deliberately has no retry or provider-failover behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from math import isfinite
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_MAX_REQUEST_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
HARD_MAX_BODY_BYTES = 16 * 1024 * 1024


class HttpTransportError(RuntimeError):
    """A sanitized transport failure that never includes headers or bodies."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    json_body: Mapping[str, Any]
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> HttpResponse: ...


OpenFunction = Callable[..., Any]


class UrllibTransport:
    """Perform one bounded HTTPS JSON exchange using the standard library.

    ``opener`` is injectable so tests and hosts can supply an audited boundary.
    It must accept a :class:`urllib.request.Request` plus ``timeout=`` and return
    a response compatible with ``urllib.request.urlopen``.
    """

    def __init__(
        self,
        *,
        opener: OpenFunction | None = None,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._opener = opener or urlopen
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
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise HttpTransportError("transport requires an HTTPS endpoint")
        if parsed.username or parsed.password or parsed.fragment:
            raise HttpTransportError("transport endpoint contains forbidden URL data")
        normalized_method = str(method).upper()
        if normalized_method != "POST":
            raise HttpTransportError("transport supports POST requests only")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise HttpTransportError("transport timeout must be positive and finite")
        try:
            payload = json.dumps(
                dict(json_body),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise HttpTransportError("request body is not JSON serializable") from exc
        if len(payload) > self.max_request_bytes:
            raise HttpTransportError("request JSON exceeds the configured byte limit")

        request = Request(
            url,
            data=payload,
            headers={str(key): str(value) for key, value in headers.items()},
            method=normalized_method,
        )
        try:
            response = self._opener(request, timeout=timeout_seconds)
        except HTTPError as exc:
            # HTTPError is also a readable response.  Return its bounded JSON so
            # the provider adapter can apply its normal sanitized error policy.
            response = exc
        except Exception:
            raise HttpTransportError("HTTPS request failed") from None

        try:
            try:
                body = _read_bounded(response, self.max_response_bytes)
                status_code = _status_code(response)
                response_headers = _headers(response)
            except HttpTransportError:
                raise
            except Exception:
                raise HttpTransportError("HTTPS response read failed") from None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpTransportError("response is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, Mapping):
            raise HttpTransportError("response JSON root must be an object")
        return HttpResponse(
            status_code=status_code,
            json_body=dict(decoded),
            headers=response_headers,
        )


def _bounded_limit(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 1 or value > HARD_MAX_BODY_BYTES:
        raise ValueError(f"{name} must be in [1, {HARD_MAX_BODY_BYTES}]")
    return value


def _read_bounded(response: Any, maximum: int) -> bytes:
    headers = getattr(response, "headers", None)
    content_length = None
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            content_length = getter("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > maximum:
                raise HttpTransportError("response JSON exceeds the configured byte limit")
        except (TypeError, ValueError):
            pass
    body = response.read(maximum + 1)
    if not isinstance(body, bytes):
        raise HttpTransportError("response body must be bytes")
    if len(body) > maximum:
        raise HttpTransportError("response JSON exceeds the configured byte limit")
    return body


def _status_code(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        getter = getattr(response, "getcode", None)
        value = getter() if callable(getter) else None
    if isinstance(value, bool) or not isinstance(value, int):
        raise HttpTransportError("response has no valid HTTP status")
    return value


def _headers(response: Any) -> dict[str, str]:
    raw = getattr(response, "headers", None)
    items = getattr(raw, "items", None)
    if not callable(items):
        return {}
    return {str(key): str(value) for key, value in items()}
