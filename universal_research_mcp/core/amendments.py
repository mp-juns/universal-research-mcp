"""Fail-closed resolved views for append-only core amendments."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _corrects_target(amendment: dict[str, Any]) -> str:
    targets = [
        relation.get("target_id")
        for relation in amendment.get("relations", [])
        if isinstance(relation, dict) and relation.get("type") == "corrects"
    ]
    if len(targets) != 1 or not isinstance(targets[0], str):
        raise ValueError(f"{amendment.get('record_id')}: amendment requires one corrects target")
    return targets[0]


def _payload_key(pointer: Any) -> str:
    if not isinstance(pointer, str) or not pointer.startswith("/payload/"):
        raise ValueError("core amendments may only resolve an existing top-level payload field")
    key = pointer.removeprefix("/payload/")
    if not key or "/" in key:
        raise ValueError("core amendment path must target one payload field")
    return key.replace("~1", "/").replace("~0", "~")


def resolve_core_amendments(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build a derived resolved view without modifying any canonical record.

    The intentionally narrow path contract prevents an amendment from silently
    changing identity, ownership, approval, or artifact lineage.
    """

    resolved = deepcopy(records)
    index = {str(record.get("record_id")): position for position, record in enumerate(resolved) if record.get("record_id")}
    if len(index) != len([record for record in resolved if record.get("record_id")]):
        raise ValueError("duplicate core record ID")
    applied: list[dict[str, Any]] = []

    for amendment in records:
        if amendment.get("schema_version") != "core/1.0" or amendment.get("record_kind") != "amendment":
            continue
        if amendment.get("status") != "completed":
            raise ValueError(f"{amendment.get('record_id')}: only completed amendments can affect a resolved view")
        target_id = _corrects_target(amendment)
        if target_id not in index:
            raise ValueError(f"{amendment.get('record_id')}: amendment target is absent")
        payload = amendment.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"{amendment.get('record_id')}: amendment payload is required")
        key = _payload_key(payload.get("path"))
        target = resolved[index[target_id]]
        target_payload = target.get("payload")
        if not isinstance(target_payload, dict) or key not in target_payload:
            raise ValueError(f"{amendment.get('record_id')}: target payload field is absent")
        if target_payload[key] != payload.get("recorded_value"):
            raise ValueError(f"{amendment.get('record_id')}: recorded value does not match current resolved value")
        target_payload[key] = deepcopy(payload.get("corrected_value"))
        applied.append({"amendment_id": amendment.get("record_id"), "target_id": target_id, "path": payload["path"]})

    return resolved, applied
