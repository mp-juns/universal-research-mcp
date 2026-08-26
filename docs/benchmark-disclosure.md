# Benchmark Disclosure

## Status

The repository contains a paired A/B protocol, fixtures, schemas, and scoring
contracts. Its [completed-results index](../benchmarks/results/README.md)
collects five separate development or integration reports. None is a
confirmatory product-effect result. Do not pool their observations: task sets,
interfaces, budgets, repetitions and evaluation methods differ.

The earlier reports include both negative overhead findings and narrow
source-mutation safety observations. The 96-run public four-arm study used a
separate condition-blinded LLM evaluator, not human review; its paired unsafe-
assertion interval includes zero. These limitations remain in the original
reports and are not replaced by a more favorable aggregate.

The latest completed
[A/B/C integration diagnostic](../benchmarks/results/abc-integration-diagnostic-20260826.md)
used one public synthetic task, no repetitions, and deterministic exact scoring
with no human or LLM evaluator. Its three tool-backed responses followed seven
startup diagnostics. All ten attempts remain represented; the filesystem
input-ceiling stop and four unknown-usage attempts are not discarded. Input
ceilings differed (64,000 versus 128,000), so the observed 0/1, 0/1 and 1/1
strict scores do not establish superiority or a causal effect.

This is a source-checkout integration observation, not a released-package
efficacy measurement or an attested production-batch execution. The public
projection has its own content hash and preserves original artifact hashes;
those hashes do not recreate omitted private archives or discarded raw streams.
No in-progress experiment results, session logs, approval records or local
execution paths are included in the new publication.

## Usage and cost

Provider-reported usage is distinguished from missing usage. Missing values
remain unavailable, never zero. Historical reports without a pricing basis
remain unpriced. The completed integration diagnostic additionally gives a
fixed-list-price normalization, explicitly separate from actual provider
billing and current pricing. Its reservation for unknown usage is an
accounting convention, not measured usage or proof of a billing ceiling.

## Required future protocol

Every pair must hold model/revision, prompt, task, permission envelope, source
snapshot, budgets, evaluator rubric, and runtime configuration constant except
for the registered retrieval condition. Runs use paired repetitions, retain
started failures/timeouts/retries, preserve raw host telemetry, and report
paired differences rather than unsupported aggregate claims.

Public reports may include citation correctness, unsupported-claim rate,
evidence retrieval success, total tokens, tool calls, latency, cost, and paired
differences, with scope-specific definitions and missing-data denominators.
Static regression checks and zero-model simulations must be labeled separately
from observed participant-model performance. A source hash or eligibility
receipt does not itself prove semantic support or scientific truth.

See [`benchmarks/protocols/mcp-ab-v1.md`](../benchmarks/protocols/mcp-ab-v1.md)
for the preregistered method and failure-retention rules.
