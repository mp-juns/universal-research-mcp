"""Fail-closed claim inventory and final-answer rendering."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from universal_research_mcp.governance.hashing import artifact_hash

from .contracts import HarnessContractError, classify_claim


def evaluate_segments(
    segments: object,
    *,
    verification_mode: str,
    verification_receipts: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if not isinstance(segments, list) or not segments:
        raise HarnessContractError("structured answer requires at least one segment")
    receipts: dict[str, Mapping[str, Any]] = {}
    for receipt in verification_receipts:
        if not isinstance(receipt, Mapping):
            raise HarnessContractError("verification receipt must be an object")
        claim_id = receipt.get("claim_id")
        if not isinstance(claim_id, str) or claim_id in receipts:
            raise HarnessContractError("verification receipt claim binding is invalid")
        if receipt.get("receipt_hash") != artifact_hash({key: value for key, value in receipt.items() if key != "receipt_hash"}):
            raise HarnessContractError("verification receipt hash mismatch")
        receipts[claim_id] = receipt
    evaluated: list[dict[str, Any]] = []
    rendered: list[str] = []
    blocked: list[str] = []
    for item in segments:
        if not isinstance(item, Mapping):
            raise HarnessContractError("answer segment must be an object")
        allowed = {
            "claim_id", "text", "kind", "final", "external", "numerical",
            "citation", "benchmark", "causal", "canonical", "conflicting",
            "evidence_refs",
        }
        unknown = sorted(set(item) - allowed)
        if unknown:
            raise HarnessContractError(f"answer segment contains unsupported fields: {', '.join(unknown)}")
        evidence = item.get("evidence_refs", [])
        if not isinstance(evidence, list) or any(not isinstance(ref, str) or not ref for ref in evidence):
            raise HarnessContractError("evidence_refs must be an array of strings")
        classification = classify_claim(
            {
                **{key: value for key, value in item.items() if key not in {"text", "evidence_refs"}},
                "statement": item.get("text"),
            },
            verification_mode=verification_mode,
        )
        receipt = receipts.get(classification["claim_id"])
        eligible = classification["level"] == "L0"
        if classification["level"] == "L1":
            eligible = bool(receipt and receipt.get("retrieval_passed") is True and evidence)
        elif classification["level"] == "L2":
            eligible = bool(receipt and receipt.get("source_verification_passed") is True and evidence)
        elif classification["level"] == "L3":
            eligible = bool(
                receipt
                and receipt.get("source_verification_passed") is True
                and receipt.get("independent_review_passed") is True
                and evidence
            )
        if receipt and sorted(receipt.get("evidence_refs") or []) != sorted(evidence):
            eligible = False
        record = {**classification, "evidence_refs": evidence, "eligible": eligible}
        evaluated.append(record)
        if eligible:
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise HarnessContractError("answer segment text is required")
            rendered.append(text.strip())
        else:
            blocked.append(classification["claim_id"])
    return {
        "schema_version": "claim-render-result/1.0",
        "status": "passed" if not blocked else "blocked",
        "answer": "\n\n".join(rendered),
        "claims": evaluated,
        "blocked_claim_ids": blocked,
        "claim_eligibility": "eligible" if not blocked else "blocked",
    }


def output_schema() -> dict[str, Any]:
    boolean_fields = {
        name: {"type": "boolean"}
        for name in ("final", "external", "numerical", "citation", "benchmark", "causal", "canonical", "conflicting")
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["segments"],
        "properties": {
            "segments": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "claim_id", "text", "kind", *boolean_fields, "evidence_refs",
                    ],
                    "properties": {
                        "claim_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},
                        "text": {"type": "string", "minLength": 1},
                        "kind": {"enum": ["creative", "interpretation", "factual", "recommendation", "result"]},
                        **boolean_fields,
                        "evidence_refs": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    },
                },
            },
        },
    }
