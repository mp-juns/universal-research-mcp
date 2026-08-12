# Integrity & Claim-Gating v1: Development Run (2026-08-13)

## Status

Completed **development instrumentation**, not a confirmatory product study.
The public corpus contains 24 synthetic tasks, all completed once in each of
four conditions (96 participant runs). The participant and the separate
condition-blinded evaluator both used `gpt-5.6-terra` with low reasoning
effort. The evaluation was not human review.

The evaluator received only a random evaluation ID, question, task-specific
gold rubric, reference packet, and final answer. It did not receive the
condition name, trial ID, tool trace, execution tokens, or execution latency.
Raw answers and traces remain outside the repository; their hashes are in the
machine-readable result.

## Observed results

| Condition | Unsafe material assertions / 18 fault tasks | Clean supported-claim coverage | Mean execution tokens | Mean latency |
| --- | ---: | ---: | ---: | ---: |
| Filesystem | 4 / 18 (22.2%) | 66.7% | 73,596 | 23.05 s |
| Filesystem + manifest | 4 / 18 (22.2%) | 16.7% | 67,962 | 23.18 s |
| MCP evidence-only | 6 / 18 (33.3%) | 100.0% | 90,047 | 29.70 s |
| MCP + Claim Gate | 2 / 18 (11.1%) | 100.0% | 113,951 | 37.21 s |

For the paired filesystem → MCP + Claim Gate comparison, the observed
unsafe-assertion difference was **−11.1 percentage points**. Its task-bootstrap
95% interval was **−33.3 to +11.1 percentage points**, which includes zero.
The MCP + Claim Gate condition therefore has an observed signal but no
statistically decisive development-sample result.

Relative to filesystem, MCP + Claim Gate used **+40,355 mean execution
tokens** (1.55×) and **+14.16 s mean latency** (1.61×). Provider-billed cost
was unavailable and is not estimated. The separate blinded evaluation consumed
656,420 provider-reported tokens; that analysis cost is reported separately
and excluded from per-condition execution comparisons.

## What the mechanism did not solve

The MCP + Claim Gate condition still emitted unsafe material claims for two
fault types: conflicting current evidence and current-but-semantically-
irrelevant evidence. This is expected from the boundary: a hash-bound,
registered-evidence gate checks revision eligibility and required evidence
count; it does not prove semantic relevance, arbitrate a conflict, or validate
the scientific truth of source prose.

## Interpretation boundary

This run supports only the following narrow statement: in this synthetic,
public development corpus, an MCP workflow with the deterministic Claim Gate
had fewer evaluator-labelled invalid material assertions than the direct
filesystem condition, at a measured token and latency cost. It does not
establish general hallucination reduction, research-quality improvement,
provider/model superiority, or a causal product effect. A frozen hidden-task
confirmatory run with repeated trials and independent human review remains
required.

See the [machine-readable record](integrity-claim-gate-v1-development-20260813.json)
and the [protocol](../protocols/integrity-claim-gate-v1.md).
