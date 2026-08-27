# Fault Taxonomy v1 — Results (2026-08-28)

Preregistered decision reached: **`hash_gate_alone_insufficient`** for this
corpus. Addressable(D1) = **6.5%**, and across the entire blind-spot Wilson
interval it stays within [3.1%, 14.0%] — below the 30% baseline at every
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

## Stage B — 80 hash_match links

B1 75 · B2 5 · B3/B4/B5/B6 0. Blind-spot rate **6.25%** (Wilson 95%
[2.7%, 13.8%]). All five blind spots are one pattern: correction- or
summary-claims citing an artifact that carries only part of the claim
(partial support). The fixed B4 probe surfaced no conflicts; one plausible
supersession outside the probe's top-10 is recorded as a probe-recall
limitation.

## Synthesis

caught ≈ 61 × 3.3% = 2; missed ≈ 457 × 6.25% ≈ 28.6;
**Addressable(D1) = 6.5%** < 30% baseline → per the preregistration, file-
level hash gating alone is insufficient here, and the next builds are
(1) content-anchored evidence addressing 96.7% of alarms and (2)
source-reference backfill addressing the 23.6% broken-chain share. This
reframes the paired benchmarks: their safety wins came from the workflow's
abstain-on-alarm discipline; the alarm itself is imprecise at range level.

## Deviations from the preregistration

1. Coder roles swapped: the Claude session coded primary (machine-assisted:
   claim/token presence checks, fixed probes, micro-greps); the owner's
   independent 20-link κ pass is **pending** and the blind-spot estimate is
   provisional until it lands.
2. Extension sample used seed 20260829 (base seed + 1 day).
3. Stage C gained code `C2m` (displaced-intact) when the corpus revealed a
   structure the preregistered C1–C3 could not express without misleading
   the design decision.
4. The machine triage predicate was corrected mid-study (diff-overlap →
   slice-equality), caught by the preregistered confirmation subsample.

Link-level CSVs and coded files stay private; this report is
aggregates-only.
