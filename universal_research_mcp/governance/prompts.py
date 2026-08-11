"""Versioned, hash-bound prompt packs for the fixed governance roster.

The files in ``governance/roles/<agent_id>/instructions.md`` are the canonical
host-neutral role instructions.  This module deliberately performs no model
call and grants no authority; it only parses, validates, hashes, and renders
those static contracts for host adapters and distribution tooling.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from universal_research_mcp.governance.hashing import artifact_hash
from universal_research_mcp.governance.registry import FIXED_ROSTER, GOVERNANCE_VERSION


PROMPT_PACK_SCHEMA_VERSION = "role-prompt-pack/1.0"
PROMPT_REGISTRY_SCHEMA_VERSION = "role-prompt-registry/1.0"

_SECTION_KEYS = {
    "Mission": "mission",
    "Required Inputs": "inputs",
    "Required Outputs": "outputs",
    "Forbidden": "forbidden",
    "Activation": "activation",
    "Prompt Injection Defense": "prompt_injection",
    "Evidence Rules": "evidence",
    "Output Contract": "output_contract",
}
_HASH_FIELDS = (
    "schema_version",
    "governance_version",
    "agent_id",
    "version",
    "mission",
    "inputs",
    "outputs",
    "forbidden",
    "activation",
    "prompt_injection",
    "evidence",
    "output_contract",
)
_ARRAY_FIELDS = tuple(key for key in _SECTION_KEYS.values() if key != "mission")


class PromptPackError(ValueError):
    """Raised when a role prompt pack is missing, malformed, or mismatched."""


def _parse_front_matter(text: str) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PromptPackError("prompt pack must begin with front matter")
    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise PromptPackError("prompt pack front matter is not closed") from exc

    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise PromptPackError("front matter entries must be non-empty key/value pairs")
        normalized = key.strip()
        if normalized in metadata:
            raise PromptPackError(f"duplicate front matter key: {normalized}")
        metadata[normalized] = value.strip()
    return metadata, lines[closing + 1 :]


def _parse_sections(agent_id: str, lines: list[str]) -> dict[str, Any]:
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    if not nonempty:
        raise PromptPackError("prompt pack body is empty")
    title_index = nonempty[0]
    if lines[title_index].strip() != f"# {agent_id}":
        raise PromptPackError("prompt pack title must match agent_id")

    collected: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in lines[title_index + 1 :]:
        line = raw_line.rstrip()
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading not in _SECTION_KEYS:
                raise PromptPackError(f"unknown prompt pack section: {heading}")
            key = _SECTION_KEYS[heading]
            if key in collected:
                raise PromptPackError(f"duplicate prompt pack section: {heading}")
            collected[key] = []
            current = key
            continue
        if not line.strip():
            continue
        if current is None:
            raise PromptPackError("content appears before the first prompt pack section")
        collected[current].append(line.strip())

    missing = set(_SECTION_KEYS.values()) - set(collected)
    if missing:
        raise PromptPackError(f"prompt pack sections are missing: {', '.join(sorted(missing))}")

    mission_lines = collected["mission"]
    if not mission_lines or any(line.startswith("- ") for line in mission_lines):
        raise PromptPackError("Mission must be non-empty prose")
    parsed: dict[str, Any] = {"mission": " ".join(mission_lines)}
    for field in _ARRAY_FIELDS:
        values = collected[field]
        if not values or any(not value.startswith("- ") or not value[2:].strip() for value in values):
            raise PromptPackError(f"{field} must contain non-empty Markdown bullets")
        parsed[field] = [value[2:].strip() for value in values]
    return parsed


def validate_prompt_pack(pack: Mapping[str, Any]) -> list[str]:
    """Return deterministic validation issues for one parsed prompt pack."""

    issues: list[str] = []
    required = set(_HASH_FIELDS) | {"prompt_pack_hash"}
    missing = required - set(pack)
    unexpected = set(pack) - required
    if missing:
        issues.append(f"missing fields: {', '.join(sorted(missing))}")
    if unexpected:
        issues.append(f"unexpected fields: {', '.join(sorted(unexpected))}")
    if pack.get("schema_version") != PROMPT_PACK_SCHEMA_VERSION:
        issues.append("unsupported prompt pack schema_version")
    if pack.get("governance_version") != GOVERNANCE_VERSION:
        issues.append("prompt pack governance_version mismatch")
    if pack.get("agent_id") not in FIXED_ROSTER:
        issues.append("prompt pack agent_id is not in the fixed roster")
    if not isinstance(pack.get("version"), str) or not str(pack.get("version")).strip():
        issues.append("prompt pack version must be a non-empty string")
    if not isinstance(pack.get("mission"), str) or not str(pack.get("mission")).strip():
        issues.append("prompt pack mission must be non-empty")
    for field in _ARRAY_FIELDS:
        value = pack.get(field)
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(value) != len(set(value))
        ):
            issues.append(f"prompt pack {field} must be a non-empty unique string array")
    if not missing and pack.get("prompt_pack_hash") != prompt_pack_hash(pack):
        issues.append("prompt pack hash mismatch")
    return issues


def prompt_pack_hash(pack: Mapping[str, Any]) -> str:
    """Hash the semantic prompt contract without its self-referential hash."""

    return artifact_hash({field: pack.get(field) for field in _HASH_FIELDS})


def _read_prompt_pack(agent_id: str, roles_root: Path | None) -> str:
    if roles_root is not None:
        path = Path(roles_root) / agent_id / "instructions.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PromptPackError(f"cannot read prompt pack for {agent_id}") from exc
    resource = resources.files("universal_research_mcp.governance").joinpath(
        "roles", agent_id, "instructions.md",
    )
    try:
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise PromptPackError(f"cannot read packaged prompt pack for {agent_id}") from exc


def load_prompt_pack(agent_id: str, roles_root: Path | None = None) -> dict[str, Any]:
    """Load and validate one fixed-roster prompt pack from source or a wheel."""

    if agent_id not in FIXED_ROSTER:
        raise PromptPackError(f"unknown governance role: {agent_id}")
    metadata, body = _parse_front_matter(_read_prompt_pack(agent_id, roles_root))
    expected_metadata = {"schema_version", "governance_version", "agent_id", "version"}
    if set(metadata) != expected_metadata:
        raise PromptPackError("prompt pack front matter fields do not match the fixed contract")
    if metadata["agent_id"] != agent_id:
        raise PromptPackError("prompt pack front matter agent_id mismatch")
    pack: dict[str, Any] = {**metadata, **_parse_sections(agent_id, body)}
    pack["prompt_pack_hash"] = prompt_pack_hash(pack)
    issues = validate_prompt_pack(pack)
    if issues:
        raise PromptPackError("; ".join(issues))
    return pack


def load_prompt_registry(roles_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load all and only the eleven fixed prompt packs."""

    return {
        agent_id: load_prompt_pack(agent_id, roles_root)
        for agent_id in sorted(FIXED_ROSTER)
    }


def prompt_registry_report(roles_root: Path | None = None) -> dict[str, Any]:
    """Return a compact distribution/host handshake for the prompt bundle."""

    packs = load_prompt_registry(roles_root)
    hashes = {agent_id: pack["prompt_pack_hash"] for agent_id, pack in packs.items()}
    return {
        "schema_version": PROMPT_REGISTRY_SCHEMA_VERSION,
        "governance_version": GOVERNANCE_VERSION,
        "role_count": len(packs),
        "prompt_pack_hashes": hashes,
        "prompt_registry_hash": artifact_hash(hashes),
    }


def render_prompt_pack(pack: Mapping[str, Any]) -> str:
    """Render a validated host-neutral system instruction deterministically."""

    issues = validate_prompt_pack(pack)
    if issues:
        raise PromptPackError("cannot render invalid prompt pack: " + "; ".join(issues))
    headings = (
        ("Mission", "mission"),
        ("Required Inputs", "inputs"),
        ("Required Outputs", "outputs"),
        ("Forbidden", "forbidden"),
        ("Activation", "activation"),
        ("Prompt Injection Defense", "prompt_injection"),
        ("Evidence Rules", "evidence"),
        ("Output Contract", "output_contract"),
    )
    lines = [
        f"Registered governance role: {pack['agent_id']}",
        f"Prompt pack: {pack['schema_version']} {pack['version']} {pack['prompt_pack_hash']}",
        "This prompt pack narrows behavior but grants no execution, write, network, model, or approval authority.",
    ]
    for heading, field in headings:
        lines.extend(("", f"{heading}:"))
        value = pack[field]
        if field == "mission":
            lines.append(str(value))
        else:
            lines.extend(f"- {item}" for item in value)
    return "\n".join(lines) + "\n"
