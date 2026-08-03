"""Deterministic local query expansion for project-specific terminology."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


ALIASES_PATH = Path(__file__).with_name("query_aliases.json")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.lower().split())


@lru_cache(maxsize=1)
def load_alias_rules() -> list[dict[str, Any]]:
    payload = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))

    if not isinstance(payload, list):
        raise ValueError("query_aliases.json must contain a JSON array")

    return payload


def rule_matches(rule: dict[str, Any], normalized_query: str) -> bool:
    groups = rule.get("match_all_any", [])

    if not groups:
        return False

    # Each outer group must match at least one of its trigger expressions.
    for group in groups:
        if not any(
            normalize_text(str(trigger)) in normalized_query
            for trigger in group
        ):
            return False

    return True


def build_query_variants(
    query: str,
    max_variants: int = 3,
) -> list[dict[str, Any]]:
    original = " ".join(query.split())

    if not original:
        return []

    normalized_query = normalize_text(original)

    variants: list[dict[str, Any]] = [
        {
            "name": "original",
            "query": original,
            "weight": 1.0,
        }
    ]

    seen = {normalized_query}

    for rule in load_alias_rules():
        if not rule_matches(rule, normalized_query):
            continue

        terms = [
            str(term).strip()
            for term in rule.get("terms", [])
            if str(term).strip()
        ]
        expanded_query = " ".join(terms)
        normalized_expansion = normalize_text(expanded_query)

        if not expanded_query or normalized_expansion in seen:
            continue

        seen.add(normalized_expansion)
        variants.append(
            {
                "name": str(rule.get("name", "project_alias")),
                "query": expanded_query,
                "weight": float(rule.get("weight", 0.95)),
            }
        )

        if len(variants) >= max_variants:
            break

    return variants
