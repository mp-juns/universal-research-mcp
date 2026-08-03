"""Canonical JSON hashing for tamper-evident governance artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    # Governance hashes must bind standards-compliant JSON.  Python's default
    # encoder accepts NaN and infinities even though they are not JSON values;
    # allowing them would make budget and scope material ambiguous across
    # implementations.
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def artifact_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def hash_without(value: dict[str, Any], field: str) -> str:
    return artifact_hash({key: item for key, item in value.items() if key != field})
