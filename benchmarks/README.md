# MCP A/B Benchmark

This directory contains a preregistration-ready, provider-neutral benchmark
contract. It prepares the experiment but does not authorize or execute live
model/API calls. No confirmatory live A/B result exists for this repository.

For already completed observations, start with the
[completed-results index](results/README.md). It keeps the directional,
claim-safety, authority-safety, four-arm development and one-task A/B/C
integration reports separate. Do not pool their trials or treat startup
diagnostics, static tests, or simulations as efficacy measurements.

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

## Integrity & Claim-Gating v1

`integrity-claim-gate-v1` is a separate development-instrumentation protocol.
It does not replace the broad `mcp-ab-v1` benchmark or establish a measured
product effect. Its co-primary measurements are deliberately narrower:

- **Integrity-fault Unsafe Assertion Rate**: invalid material assertions when
  evidence is changed, stale, missing, conflicting, withdrawn, or otherwise
  ineligible; and
- **Clean Supported Claim Coverage**: correct, complete, source-bound answers
  when current evidence is sufficient.

It also reports provider tokens, latency, tool calls, false blocks,
gate-invocation recall, routine over-gating, exact evidence binding, and
citation support separately. Four arms distinguish filesystem retrieval,
filesystem plus checksum manifest, MCP evidence flow, and MCP plus material
claim gate.

Files:

- `protocols/integrity-claim-gate-v1.md`: the scoped protocol and reporting
  boundary;
- `config/integrity-claim-gate-v1.json`: the frozen 24-task/96-run development
  instrumentation design; and
- `fixtures/integrity-claim-gate-v1/tasks.development.jsonl`: public task
  metadata for harness/evaluator calibration only, never a confirmatory holdout.

Use `scripts/validate_integrity_claim_gate_benchmark.py` to validate a run file
or emit its safety–coverage–burden report.

A 96-run public development execution is available in
[`results/integrity-claim-gate-v1-development-20260813.md`](results/integrity-claim-gate-v1-development-20260813.md).
It used 24 synthetic public tasks once per condition and a separate
condition-blinded model evaluator. Its paired confidence interval includes
zero, so it must be read as development instrumentation rather than a product
effect or a general research-quality claim.

Before a live run, a human must approve the final task-set fingerprint, model,
provider, pricing snapshot, budgets, source-bundle fingerprint, and execution
command. API credentials may be supplied only through the process environment
or an approved secret store and must never enter a task, trace, ledger, log, or
Git commit.

## Completed A/B/C integration diagnostic

The [2026-08-26 integration report](results/abc-integration-diagnostic-20260826.md)
contains three tool-backed responses to one public synthetic task, plus the
seven preceding startup attempts. It uses deterministic exact scoring with
zero human evaluators and zero LLM judges. Filesystem retains its original
64,000-input ceiling stop; the other two conditions used a revised 128,000
diagnostic ceiling. The strict scores 0/1, 0/1 and 1/1 are not a uniform-budget
comparison, repeated experiment, or confirmatory result.

Its [sanitized JSON projection](results/abc-integration-diagnostic-20260826.json)
preserves all ten attempt identities, known usage and four unknown-usage rows.
It omits operational logs, local paths, authority records and raw model streams.
Unknown usage is not converted to zero; normalized list-price accounting is
separate from unavailable actual provider billing. No in-progress trial data
is published here.
