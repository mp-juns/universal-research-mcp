# Fault Taxonomy v1 — Results (2026-08-28)

Preregistered decision reached: **`hash_gate_alone_insufficient`** for this
corpus. Addressable(D1) = **5.5%**, and across the entire blind-spot Wilson
interval it stays within [2.7%, 11.0%] — below the 30% baseline at every
point, so the decision is CI-robust.

## Stage A — census (686 links, exhaustive)

| status | n | share |
| --- | ---: | ---: |
| hash_match | 457 | 66.6% |
| hash_mismatch | 61 | 8.9% |
| ref_without_hash | 133 | 19.4% |
| no_ref | 20 | 2.9% |
| hash_unrecoverable | 9 | 1.3% |
| range_invalid | 6 | 0.9% |

Gate visibility D1 = 67/533 = **12.6%**. Broken-chain share (links the gate
can never bind) = **23.6%**. The third registration tertile shows the
hash-recording practice collapsing (127 hash-less references after
2026-07-25 versus 0 before 2026-07-15) — the measured false-block cause,
located in time; the survivorship threat ran in the opposite direction.

## Stage C — the 61 mismatches, machine-decomposed

| code | n | share | meaning |
| --- | ---: | ---: | --- |
| C1 slice-identical | 32 | 52.5% | cited lines byte-identical; alarm caused elsewhere in file |
| C2m displaced-intact | 27 | 44.3% | cited block verbatim at a shifted position (append-style growth) |
| C3 substantive | 2 | 3.3% | cited content replaced/rewritten |

Substantive-mismatch rate = 2/61 = **3.3%**. The initial diff-overlap triage
predicate was wrong (it missed displacement); the preregistered seeded
confirmation subsample caught it at 5/10 and the predicate was corrected to
slice-equality before coding — recorded as the protocol intended.

Design consequence: line-number range hashing would rescue only the 32 C1
links and still block the 27 displaced ones; the fix that addresses 59/61
alarms is **content-anchored (passage-hash) evidence**, whose bones the
store's passage system already has.

**Measured, not projected**: the retrospective replay
(`replay_content_anchor.py`, zero model tokens, read-only) re-verdicts the
same 686 links under content-anchored semantics: the 61 alarms collapse to
exactly the 2 substantive (C3) links — 32 pass in place, 27 pass displaced
— while all 457 previously passing links keep passing and no new alarm
appears (`results/content-anchor-replay.json`, design in
`docs/decisions/0004-content-anchored-evidence.md`).

## Stage B — 80 hash_match links (79 rated after one B0 exclusion)

B1 73 · B2 6 · B0 1 · B3/B4/B5/B6 0. Blind-spot rate **7.6%** (6/79,
Wilson 95% [3.5%, 15.6%]). All six blind spots are one family — partial
support: a claim citing an artifact that carries only part of it. Five are
correction- or summary-claims over partial artifacts (one of them a
degenerate *registered* range: line 1-1 explicitly recorded against a
682-line artifact); the sixth is a release claim whose ancestry component
("0.8.1-descended") lies outside the registered range. The fixed B4 probe
surfaced no conflicts; one plausible supersession outside the probe's
top-10 is recorded as a probe-recall limitation.

## Reliability (two-coder pass, 20 links, seed 20260830)

Second coder: GPT-5.6 via `codex exec`, read-only on the corpus, codebook
only, blind to the primary codes. Initial raw agreement **15/20**;
Cohen's κ = **0.000** — a textbook kappa paradox (the primary marginal was
constant, 20/20 B1, so chance-agreement equals raw agreement). The
prevalence-robust estimate is **Gwet AC1 = 0.729**, and that
pre-adjudication figure is the honest reliability estimate for this study.

The five disagreements produced codebook v1.1 clarifications (B0 unratable;
B5 supersession scope; B2 interpretive boundary — recorded deviation) and a
rerun on those five: three converged (#41 B1, #49 B1, #63 B0). The two
residuals were resolved evidence-based, with owner adjudication deferred at
the owner's direction:

- **#42** — instrumentation error on our side: the κ-sheet printed "range
  1-1" for a citation that registers no line fields (path+sha256 =
  whole-file). With the corrected stimulus the second coder returned B1;
  converged. Census hash semantics were always whole-file and are
  unaffected.
- **#75** — the second coder was right: the registered range (WORK_LOG.md
  279-288) supports release/attestation/E2E but contains no ancestry
  token at all; the primary code flipped to B2.

Final adjudicated agreement 20/20; the reported blind-spot rate uses the
adjudicated codes.

## Synthesis

caught ≈ 61 × 3.3% = 2; missed ≈ 457 × 7.6% ≈ 34.7;
**Addressable(D1) = 5.5%** < 30% baseline → per the preregistration, file-
level hash gating alone is insufficient here, and the next builds are
(1) content-anchored evidence addressing 96.7% of alarms and (2)
source-reference backfill addressing the 23.6% broken-chain share. This
reframes the paired benchmarks: their safety wins came from the workflow's
abstain-on-alarm discipline; the alarm itself is imprecise at range level.

## Deviations from the preregistration

1. Coder roles swapped: the Claude session coded primary (machine-assisted:
   claim/token presence checks, fixed probes, micro-greps); the independent
   second coder was a model (GPT-5.6, read-only) rather than the owner, and
   residual adjudication was evidence-based with the owner's pass
   explicitly deferred at the owner's direction.
2. Extension sample used seed 20260829 (base seed + 1 day).
3. Stage C gained code `C2m` (displaced-intact) when the corpus revealed a
   structure the preregistered C1–C3 could not express without misleading
   the design decision.
4. The machine triage predicate was corrected mid-study (diff-overlap →
   slice-equality), caught by the preregistered confirmation subsample.
5. Codebook clarified to v1.1 after the first reliability pass (B0
   unratable; B5 scope; B2 interpretive boundary); the five disputed items
   were re-coded under v1.1.
6. κ-sheet presentation bug: citations with no registered line fields were
   printed as "range 1-1"; corrected to whole-file semantics for the
   affected re-code. Census statuses (whole-file hash comparison) were
   never affected.

Link-level CSVs and coded files stay private; this report is
aggregates-only.
