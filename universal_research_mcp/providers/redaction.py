"""Fail-safe sanitization for provider errors and structured diagnostics."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|private[_-]?key|secret|token|x-api-key)",
    re.IGNORECASE,
)
_HEADER_VALUE = re.compile(
    r"(?i)\b(authorization|x-api-key|api-key)\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_JSON_VALUE = re.compile(
    r'(?i)(["\']?(?:api[_-]?key|credential|password|secret|token)["\']?\s*[:=]\s*)["\']?[^\s,;}"\']+'
)


def redact_text(value: object, secrets: Iterable[str] = ()) -> str:
    text = str(value)
    for secret in sorted({secret for secret in secrets if secret}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = _HEADER_VALUE.sub(lambda match: f"{match.group(1)}: {REDACTED}", text)
    return _JSON_VALUE.sub(lambda match: f"{match.group(1)}{REDACTED}", text)


def sanitize(value: Any, secrets: Iterable[str] = ()) -> Any:
    materialized = tuple(secret for secret in secrets if secret)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _SENSITIVE_KEY.search(str(key)) else sanitize(item, materialized)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize(item, materialized) for item in value]
    if isinstance(value, bytes):
        return redact_text(value.decode("utf-8", errors="replace"), materialized)
    if isinstance(value, str):
        return redact_text(value, materialized)
    return value


def safe_exception(exc: BaseException, secrets: Iterable[str] = ()) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": redact_text(exc, secrets),
    }
