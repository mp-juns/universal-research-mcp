# MCP A/B Benchmark

This directory contains a preregistration-ready, provider-neutral benchmark
contract. It prepares the experiment but does not authorize or execute live
model/API calls. No confirmatory live A/B result exists for this repository.

Primary comparison:

- `filesystem`: the agent receives the same immutable source snapshot through
  ordinary read-only file search/fetch capabilities, with no research MCP or
  MCP-specific prompt.
- `mcp`: the agent retains the same ordinary read-only file capabilities and
  additionally receives Universal Research MCP candidate search, evidence
  fetch, integrity, and audit tools.

The model, prompt content, task, source snapshot, budgets, runtime class, and
evaluation rubric must otherwise be identical. An MCP-only replacement arm or
`no_retrieval` arm may be used only as a diagnostic and is not the primary
"attached versus not attached" comparison.

Files:

- `protocols/mcp-ab-v1.md`: complete preregistered method and isolation rules
- `config/mcp-ab-v1.json`: machine-readable frozen design
- `schemas/`: task/run/trace JSON Schema contracts
- `fixtures/`: synthetic contract fixtures, never project research data
- `contracts.py`: fail-closed dependency-free validation
- `scoring.py`: paired quality, token, call, latency, and cost summaries; any
  missing host/provider telemetry remains `unavailable`, never estimated

Before a live run, a human must approve the final task-set fingerprint, model,
provider, pricing snapshot, budgets, source-bundle fingerprint, and execution
command. API credentials may be supplied only through the process environment
or an approved secret store and must never enter a task, trace, ledger, log, or
Git commit.
