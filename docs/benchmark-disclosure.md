# Benchmark Disclosure

## Status

The repository contains a paired A/B protocol, fixtures, schemas, and scoring
contracts. It also publishes one exploratory, live Codex diagnostic in
[`benchmarks/results/codex-directional-v1.md`](../benchmarks/results/codex-directional-v1.md).
That diagnostic is not a confirmatory benchmark: it has four ordinary tasks,
one adversarial integrity task, one run per condition, synthetic source data,
and a Codex-specific execution boundary. It must not be presented as a general
quality, cost, latency, or provider comparison.

## Required future protocol

Every pair must hold model/revision, prompt, task, permission envelope, source
snapshot, budgets, evaluator rubric, and runtime configuration constant except
for the registered retrieval condition. Runs use paired repetitions, retain
started failures/timeouts/retries, preserve raw host telemetry, and report
paired differences rather than unsupported aggregate claims.

Public reports may include only citation correctness, unsupported-claim rate,
evidence retrieval success, total tokens, tool calls, latency, cost, and paired
differences. Telemetry that the host/provider does not supply is recorded as
`unavailable`; it is never estimated or presented as zero.

See [`benchmarks/protocols/mcp-ab-v1.md`](../benchmarks/protocols/mcp-ab-v1.md)
for the preregistered method and failure-retention rules.
