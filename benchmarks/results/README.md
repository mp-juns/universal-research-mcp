# Completed development results

This index covers completed, curated artifacts only. No in-progress experiment
results or operational session logs are included. Every report is exploratory
or an integration diagnostic, not a confirmatory product-effect study.

## Separate evidence sets

| Report | Completed scope | Main observation | Important limitation |
| --- | --- | --- | --- |
| [Codex directional v1](codex-directional-v1.md) | Four ordinary tasks plus one mutation task, two conditions, ten runs | Both conditions answered 4/4 ordinary tasks with citations; MCP used 2.26x input tokens and 2.02x latency there. The mutation case produced filesystem assertion versus MCP abstention. | MCP-only diagnostic interface, one run per condition; no ordinary quality advantage observed. |
| [Claim-safety instrumentation v3](codex-claim-safety-v3.md) | 28 runs, 14 per condition | Changed source accepted as verified evidence in 6/6 filesystem versus 0/6 MCP-gated mutation trials. Total input-token and latency ratios were 1.94x and 1.84x. | Six mutation trials cover four questions, including repeats; not general hallucination reduction. |
| [Authority-safety pilot v1](authority-safety-pilot-v1.md) | Four scenarios, two conditions, eight runs | Safe outcomes were 3/4 filesystem and 4/4 MCP-gated. Both arms were safe on missing, conflicting and withdrawn evidence; the difference occurred on source mutation. | One run per scenario/condition and a diagnostic interface comparison. |
| [Integrity/evidence-eligibility development v1](integrity-claim-gate-v1-development-20260813.md) | 24 public synthetic tasks x four conditions, 96 participant runs | Unsafe assertions were 4/18 filesystem versus 2/18 gated; clean coverage was 66.7% versus 100%. Gated mean token/latency costs were 1.55x/1.61x. | Separate blinded LLM evaluator, no human review; paired 95% interval includes zero. Evidence-only had 6/18 unsafe assertions. |
| [A/B/C integration diagnostic, 2026-08-26](abc-integration-diagnostic-20260826.md) | One public task, three tool-backed responses, plus seven preceding startup diagnostics | Strict automatic scores were A 0/1, B 0/1, C 1/1. A's input-ceiling stop and all ten diagnostic attempts remain represented. | Unequal 64k/128k input budgets, no repetitions, no human or LLM judges; not an efficacy comparison. |

These rows are **not a pooled benchmark**. Shared synthetic scenarios, task
reuse, different interfaces and different evaluation methods prevent treating
their sum as an independent sample size or a common success rate. In particular,
the earlier 28-run pilot is not part of the later one-task integration result.

## Reading the evidence

- Source integrity and evidence eligibility are narrower than semantic support
  or truth. The four-arm study still had gated failures on conflicting and
  semantically irrelevant current evidence.
- Correct abstention must be read alongside correct supported answers and
  false abstention; refusing every question is not research-quality success.
- Model-reported usage, missing usage, normalized accounting and actual billing
  are separate quantities. Never interpret missing telemetry as zero cost.
- The integration JSON is a sanitized projection with its own hash, not the
  original receipt, a public raw-trace archive, or execution attestation.
- Failed diagnostic attempts and scientific negative observations remain
  visible. Static tests and zero-model simulations are not participant results.

See the [benchmark disclosure](../../docs/benchmark-disclosure.md) for the
cross-study limits and the [benchmark contracts](../README.md) for protocol
requirements. The supported product term is **evidence eligibility**; older
artifact names retain “claim gate” for provenance.
