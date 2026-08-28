# Trace audit of the paired benchmarks (2026-08-28, zero model tokens)

Post-hoc audit of the existing 144 + 162 trial streams, answering four
questions the headline tables did not. No new runs; deterministic parsing
of the recorded tool-call events (`trace-audit-20260828.json`).

## 1. claim_type self-report fidelity — measured, but conditionally

Across all 153 gate calls in both benches, the claim_type argument matched
the task's gold type exactly (0 downgrades, 0 materiality downgrades).
**Read this narrowly**: the runner prompt prescribed the claim type, so
this measures instruction-following under scaffold, not self-report honesty
in natural sessions. The natural-session ablation measures the latter; a
self-report gate is bypassable in principle and this audit does not refute
that.

## 2. Where blocked runs actually end

70 gate-arm runs hit at least one ineligible verdict. Endpoints: 57 ended
in explicit abstention, 12 ended in a deterministically-correct release
(e.g. conflict tasks presenting both values, or recovery to genuinely
eligible evidence), 1 ended in a release outside both categories, and **0
ended in an unsafe planted-value assertion**. Evidence shopping —
re-calling the gate with different evidence after a block and then
releasing — occurred **0 times**; `suggested_registered_evidence` did not
induce it in these streams. (Caveat: the suggestion field shipped in the
perf pass; only the perf-pass runs exercised it, so this is a bounded
observation, not proof of safety.)

## 3. Tool-calls per run: medians, not tails

| stratum | filesystem | MCP + gate |
| --- | --- | --- |
| synthetic clean | 3 (IQR 3–3) | 4 (4–4) |
| synthetic fault | 3 (2–3, max 6) | 4 (4–4, max 19) |
| real clean | 4 (3–4) | 3 (3–4) |
| real fault | 6 (4–8, max 17) | **19 (7–33, max 74)** |

The previously-quoted "max 78" is a tail; the honest statement is: on real
fault tasks the gate arm's median call count is ~3× the filesystem arm's,
and the tail is long. On clean tasks the arms are equivalent.

## 4. Risk difference replaces McNemar-only reporting at zero cells

Primary stratum (published partitions: 45 hash-detectable synthetic pairs;
27 natural-fault real pairs), deterministic scoring:

| bench | filesystem | MCP+gate | RD | Newcombe 95% | task-cluster bootstrap 95% |
| --- | --- | --- | --- | --- | --- |
| synthetic | 22/45 | 0/45 | 0.489 | [0.329, 0.630] | [0.267, 0.711] |
| real corpus | 23/27 | 0/27 | 0.852 | [0.636, 0.941] | [0.704, 1.000] |

Deterministic counts differ from the blinded-judge counts by +1 (synthetic
fs: det 22 vs blinded 21) and −3 (real fs: det 23 vs blinded 26 — the
judge flagged three assertions of unverifiable content that carried no
planted value). Both scorers agree the gate arm is at zero in the primary
strata.

Negative controls remain outside the primary strata and remain honest:
poisoned-before-registration 6/6 unsafe in *both* arms (the gate cannot
catch what was corrupted before registration), verified-but-stale 3/3
unsafe in the gate arm (an intact hash admits a stale value — the
taxonomy's B5/staleness boundary).

## Full-population per-state table

See `unsafe_by_evidence_state` in the JSON: every fault state × arm cell,
negative controls included, so no stratum selection is hidden.
