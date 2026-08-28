# Adversarial audit v1 — governance contracts and ingest receipts (preregistered)

Committed before the audit code. Closes the two coverage gaps the README
names as unmeasured: the 11-role governance layer and the ingest-receipt
path under adversarial use. This is a **deterministic, zero-model-token**
audit — the adversary is the test harness sending malformed/forged/replayed
inputs directly to the tools, not a model. It measures whether each
documented fail-closed control actually holds; it does not measure agent
behavior (that would need a separate model-in-the-loop study).

## Threat model

An in-process caller that already reached the tool surface and tries to:
- **T1 privilege/scope escape** — smuggle a forbidden action, an
  out-of-scope path, network, model execution, or a hidden execution field
  through a governance operation/packet.
- **T2 cost-ceiling evasion** — exceed the declared cost or omit the plan
  reference a bounded operation requires.
- **T3 receipt forgery** — commit an ingest with a fabricated, wrong, or
  mismatched approval receipt id.
- **T4 replay** — reuse a valid one-time receipt to commit twice.
- **T5 draft tampering** — alter the record body or staged source between
  prepare and commit while keeping the receipt.
- **T6 approval bypass** — commit without any human approval scope, or with
  an approval that does not cover the record's study/kind.
- **T7 authority poisoning** — decision/packet whose hashes cannot be
  canonically computed (non-finite numbers, nested unknown fields).

## Endpoints

For each threat, N distinct hostile inputs; the pass criterion is
**fail-closed on 100%** (structured refusal or raised guard, never a
silent accept). Report per-threat pass/total and the aggregate; any single
non-fail-closed case is a finding, not a rate to average away. A parallel
small set of **legitimate** inputs must still succeed (no over-blocking),
reported as a false-positive check.

## Rules fixed in advance

- The audit reuses the shipped tools and validators unchanged; it adds no
  test-only leniency.
- A control that fails closed by raising is as acceptable as one returning
  a blocked receipt, provided the raise is a typed guard, not an
  unhandled crash that leaks state.
- Results are aggregates over synthetic inputs; no private corpus.
