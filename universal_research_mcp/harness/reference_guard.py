"""Fail-closed checks for technically named references in agent output."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


_BACKTICK = re.compile(r"`([^`\r\n]+)`")
_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"|(?<![A-Za-z0-9_.-])[A-Za-z0-9_-]+\.(?:py|pyi|js|ts|tsx|jsx|json|ya?ml|md|toml|ini|txt)(?![A-Za-z0-9_.-])"
)
_NAMED_TECHNICAL = re.compile(
    r"\b(?:function|method|class|module|file|script|path)\s+[`'\"]?"
    r"([A-Za-z_][A-Za-z0-9_.\-/]*)(?:\(\))?",
    re.IGNORECASE,
)
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\(\)")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _evidence_text(evidence_bundle: Mapping[str, Any] | None) -> tuple[set[str], str]:
    if not isinstance(evidence_bundle, Mapping):
        return set(), ""
    paths: set[str] = set()
    content: list[str] = []
    passages = evidence_bundle.get("passages")
    if not isinstance(passages, list):
        return paths, ""
    for passage in passages:
        if not isinstance(passage, Mapping):
            continue
        path = passage.get("path")
        if isinstance(path, str) and path:
            paths.add(path)
        body = passage.get("content")
        if isinstance(body, str):
            content.append(body)
    return paths, "\n".join(content)


def _candidates(text: str) -> set[str]:
    values: set[str] = set()
    values.update(_PATH.findall(text))
    values.update(match.group(1) for match in _NAMED_TECHNICAL.finditer(text))
    values.update(match.group(1) for match in _CALL.finditer(text))
    for match in _BACKTICK.finditer(text):
        raw = match.group(1).strip()
        if (
            "/" in raw
            or "." in raw
            or "_" in raw
            or raw.endswith("()")
            or (raw[:1].isupper() and _IDENTIFIER.fullmatch(raw))
        ):
            values.add(raw.removesuffix("()"))
    return {value.removesuffix("()") for value in values if value}


def _is_present(candidate: str, paths: set[str], content: str) -> bool:
    if candidate in paths or candidate in content:
        return True
    if _IDENTIFIER.fullmatch(candidate):
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])", content) is not None
    return False


def unverified_technical_reference_count(
    decision_body: Mapping[str, Any], evidence_bundle: Mapping[str, Any] | None,
) -> int:
    """Count code-like names in output that the exact evidence cannot support.

    This is intentionally a structural guard, not a claim that arbitrary prose
    can be perfectly interpreted. It catches explicit paths, technical-name
    phrases, callable syntax, and code-formatted identifiers.
    """

    paths, content = _evidence_text(evidence_bundle)
    candidates: set[str] = set()
    for text in _strings(decision_body):
        candidates.update(_candidates(text))
    return sum(not _is_present(candidate, paths, content) for candidate in candidates)
