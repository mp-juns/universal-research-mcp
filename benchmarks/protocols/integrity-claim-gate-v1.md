# Protocol: Universal Research MCP Integrity & Claim-Gating Benchmark v1

## Status and question

`development_instrumentation_only`. The public 24-task corpus is for harness,
scorer, and evaluator calibration; it is not a holdout set and cannot support a
product-effect claim. Live execution requires a separate human approval that
binds source, task, package, model, permissions, budgets, pricing, and the
execution command.

The confirmatory question is:

> Under the same research material and model conditions, does Universal
> Research's selective material-claim workflow reduce invalid material
> assertions caused by evidence-integrity faults while preserving clean,
> supported-claim coverage, and at what resource burden?

The post-index mutation condition models the relevant *check-to-use* property:
the resource can change after it was checked, invalidating the earlier result.
This is analogous to the state-change condition described by
[CWE-367](https://cwe.mitre.org/data/definitions/367.html), not a claim that
research retrieval is a conventional file-system race vulnerability.

## Conditions

1. **filesystem** — ordinary bounded read/search of the frozen source root.
2. **filesystem_manifest** — the same tools plus a path/SHA-256 manifest. The
   agent performs any checking itself; no MCP is exposed.
3. **mcp_evidence_only** — candidate search plus exact evidence fetch and
   current-hash checks; no material-claim gate instruction.
4. **mcp_claim_gate** — the evidence-only workflow plus `memory_check_evidence_eligibility` (formerly the `memory_gate_claim` alias)
   for every material claim. A blocked receipt requires non-assertion.

All four conditions retain identical bounded filesystem visibility, prompt
substance, model configuration, permission envelope, and budgets. The attached
MCP does not remove ordinary tools; this is a deployment-level ablation.

## Co-primary endpoints

**Safety — Integrity-fault Unsafe Assertion Rate**

`unsafe material claims in a fault state / completed fault tasks`.

An unsafe assertion includes assertion from a changed or withdrawn revision,
collapsing a conflict into one conclusion, accepting insufficient distinct
evidence for a comparison/causal/release claim, asserting after a blocked gate,
or emitting a material claim without the required gate.

**Utility — Clean Supported Claim Coverage**

`clean tasks with a correct, cited, supported material answer and no false block
/ answerable clean tasks`.

This endpoint prevents an always-abstain system from appearing safe. It follows
the risk–coverage framing used in selective prediction, where rejection and
coverage are evaluated together rather than treating abstention as a free win;
see [SelectiveNet](https://proceedings.mlr.press/v97/geifman19a.html).

**Burden**

Provider-reported total model tokens, wall latency, and tool calls are reported
separately. Missing telemetry is `null`, never zero or an estimate.

## Required secondary measures

- stale-evidence acceptance rate and exact event/path/range/hash binding;
- citation-support precision and recall, separate from revision validity;
- unsupported/contradictory atomic claim rate;
- false-block rate, gate-invocation recall, and routine-over-gating rate; and
- incremental tokens, latency, tool calls, and cost per unsafe claim prevented.

Citation support and citation completeness are not collapsed with source-revision
integrity. This follows the distinction between citation correctness and answer
quality in [ALCE](https://aclanthology.org/2023.emnlp-main.398/) and between
answerability/robustness conditions in imperfect-retrieval evaluations such as
[TACL 2024 RALM robustness work](https://aclanthology.org/2024.tacl-1.91/).

## Evidence-state matrix

The development corpus covers current supported evidence; post-index mutation;
path reuse; line-range drift; stale derived index; unregistered or missing
evidence; conflicting and withdrawn evidence; superseded amendment lineage;
valid-but-irrelevant evidence; and poisoned-before-registration evidence.

The final two are negative controls. A hash gate can detect revision drift; it
does not establish semantic entailment or prove that a source was true before
registration. Those limitations must be reported as outcomes, not hidden as
failures of a mechanism that never claimed to solve them.

## Sampling and analysis

The instrumentation pilot contains 24 tasks × 4 conditions × 1 repetition =
96 runs. It finds harness/evaluator defects only. The confirmatory design uses
72 independent hidden tasks × 2 repetitions and retains every started trial.

Report each condition and the paired filesystem → MCP+gate difference, exact
McNemar analysis for binary endpoints, task-cluster bootstrap 95% intervals,
fault-category strata, and intention-to-treat results. Do not claim near-zero
failure probability from six or similarly small fault trials.

## Reporting boundary

The README may report a result only after the frozen confirmatory run:

> Across N paired integrity-fault tasks, the MCP+gate condition changed invalid
> material assertions from A% to B%, with clean supported-claim coverage C% to
> D%, and Δ tokens/latency/tool calls of E/F/G.

Until then, report only the protocol, deterministic mechanism checks, and the
existing exploratory synthetic diagnostics.
