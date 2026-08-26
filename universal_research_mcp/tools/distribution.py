"""Fail-closed validation for the public distribution wheel."""

from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


BUNDLE_PREFIX = "share/universal-research-mcp/"
GOVERNANCE_ROLE_IDS = (
    "analysis_objectivity_auditor", "benchmark_control_auditor",
    "cold_adversarial_reviewer", "correction_executor",
    "paper_evidence_evaluator", "reproducibility_reviewer",
    "research_memory_maintainer", "retrieval_governor",
    "scope_and_cost_governor", "substance_reviewer", "user_alignment_reviewer",
)
REQUIRED_BUNDLE_FILES = (
    "docs/failure-policy.md", "docs/host-integration.md", "docs/secure-harness.md", "docs/security.md",
    "schemas/core-record.schema.json", "schemas/agent-runtime-event.schema.json",
    "schemas/index-health.schema.json", "schemas/pack-manifest.schema.json",
    "schemas/project-profile.schema.json", "schemas/research-agent-decision.schema.json",
    "schemas/research-agent-task.schema.json", "schemas/research-run-plan.schema.json",
    "schemas/worker-result.schema.json", "packs/study_type/research_operations.yaml",
    "plugin/universal-research-memory/.mcp.json",
    "plugin/universal-research-memory/.codex-plugin/plugin.json",
    "plugin/universal-research-memory/skills/research-workflow/SKILL.md",
    "plugin/universal-research-memory/skills/research-governance/SKILL.md",
    "plugin/universal-research-memory/skills/research-governance/agents/openai.yaml",
)
REQUIRED_RUNTIME_FILES = (
    "universal_research_mcp/governance/agent_creation.py",
    "universal_research_mcp/governance/prompts.py",
    "universal_research_mcp/integrations/codex/adapter.py",
    "universal_research_mcp/integrations/codex/agent_control.py",
    "universal_research_mcp/cli.py", "universal_research_mcp/server.py",
    "universal_research_mcp/core/indexing.py",
    "universal_research_mcp/core/input.py", "universal_research_mcp/core/ingest.py",
    "universal_research_mcp/core/canonical_io.py",
    "universal_research_mcp/indexing/lexical.py",
    "universal_research_mcp/indexing/semantic.py", "universal_research_mcp/core/redaction.py",
    "universal_research_mcp/semantic_backends.py",
    "universal_research_mcp/runtime/paths.py", "universal_research_mcp/runtime/semantic_config.py",
    "universal_research_mcp/runtime/agent_approval.py",
    "universal_research_mcp/runtime/ingest_approval.py",
    "universal_research_mcp/runtime/project_io.py",
    "universal_research_mcp/runtime/model_snapshot.py",
    "universal_research_mcp/runtime/semantic_setup.py",
    "universal_research_mcp/semantic_runtime.py", "universal_research_mcp/tools/distribution.py",
    "universal_research_mcp/tools/build_research_ledger_index.py",
    "universal_research_mcp/secure_harness/approval.py",
    "universal_research_mcp/secure_harness/contracts.py",
    "universal_research_mcp/secure_harness/controller.py",
    "universal_research_mcp/secure_harness/codex_runner.py",
    "universal_research_mcp/secure_harness/worker.py",
    "universal_research_mcp/secure_harness/worker_server.py",
)
REQUIRED_GOVERNANCE_FILES = (
    "universal_research_mcp/governance/schemas/prompt-pack.schema.json",
    *(f"universal_research_mcp/governance/roles/{agent_id}/{filename}"
      for agent_id in GOVERNANCE_ROLE_IDS for filename in ("role.yaml", "instructions.md")),
)
REMOVED_TOP_LEVEL_PREFIXES = ("core/", "governance/", "adapters/", "integrations/", "scripts/")
UNSUPPORTED_RUNTIME_PREFIXES = (
    "universal_research_mcp/providers/",
    "universal_research_mcp/agent_runtime/",
    "universal_research_mcp/harness/",
)


def validate_wheel(path: Path) -> list[str]:
    """Return required/mispackaged members found in one wheel archive."""

    if not path.is_file():
        return [f"wheel does not exist: {path}"]
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    problems = [name for name in (*REQUIRED_RUNTIME_FILES, *REQUIRED_GOVERNANCE_FILES) if name not in names]
    for relative in REQUIRED_BUNDLE_FILES:
        suffix = BUNDLE_PREFIX + relative
        if not any(name.endswith(suffix) for name in names):
            problems.append(suffix)
    problems.extend(sorted(name for name in names if name.startswith(REMOVED_TOP_LEVEL_PREFIXES)))
    problems.extend(sorted(name for name in names if name.startswith(UNSUPPORTED_RUNTIME_PREFIXES)))
    if not any(name.endswith(".dist-info/entry_points.txt") for name in names):
        problems.append("*.dist-info/entry_points.txt")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    problems = validate_wheel(args.wheel)
    if problems:
        for name in problems:
            print(f"invalid or missing wheel member: {name}")
        return 1
    print(f"validated distribution bundle: {args.wheel.name}")
    return 0
