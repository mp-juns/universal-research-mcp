# Protocol: Real-Corpus Fault Taxonomy v1 (preregistered)

Status: `preregistered_awaiting_stage_B_C_coding`. Stage A (machine census)
carries no model tokens and runs on registration; Stages B/C are human
coding. Every parameter below is fixed before coding begins; deviations are
recorded in the eventual report. Literature grounds are hash-bound in
[`real-corpus-fault-taxonomy-v1.citations.json`](real-corpus-fault-taxonomy-v1.citations.json)
(manifest sha256 `27c280a2…8fee6`) and registered as canonical evidence in a
local research store whose search→fetch→eligibility chain was probed
end-to-end; citations below use `cit_*` IDs from that manifest.

## Questions

- Primary: of the evidence links in this corpus whose registered support
  fails to back the record's claim, what fraction is caught by file-level
  hash re-verification?
- Secondary: among hash mismatches, what fraction has a substantive change
  inside the cited range? (File-level hashing blocks a lines 3–10 citation
  for a line-500 typo fix; this quantifies whether range-level hashing is
  worth building.)

## Unit and population

- Unit: evidence link = one (record, source_ref) pair; a record with two
  refs contributes two links.
- Population: every link in `research-events/daily/*/events.jsonl` of the
  reference project (read-only), all eight months. Records without refs are
  tallied separately (the stratum behind the measured 16.7% false blocks).

## Stage A — machine census (exhaustive, zero tokens)

`fault_taxonomy/census.py` classifies every link with two independent
columns:

- `link_hash_status`: `no_ref / ref_without_hash / file_missing /
  range_invalid / hash_mismatch / hash_match`, judged against the link's own
  recorded hash. `ref_without_hash` (path recorded, hash never recorded) and
  `hash_unrecoverable` (hash recorded, revision absent from git and
  worktree) are distinct classes measured in this corpus (79 and 8 distinct
  revisions respectively at design time).
- `registry_status`: whether the (path, hash) pair is in the source
  registry — kept separate so gate visibility is not conflated with
  registry coverage.

Range validity is evaluated against the registered revision when
recoverable, else against the current file, with `evaluated_against`
recorded. Output: one CSV row per link (registration date, path, record
kind, statuses, machine triage below) plus a summary with mismatch rates by
registration-date tertile (`cit_freshqa2024_change_rate_strata`: stratify
by rate of change; survivorship threat requires per-tertile reporting).

Machine triage of mismatches: for every git-recoverable `hash_mismatch`,
the census computes the intersection of the registered→current diff's
changed lines with the cited range. Empty intersection ⇒ machine label
`C1_candidate` (population-level, not sampled); non-empty ⇒ human C2/C3
adjudication. Unrecoverable mismatches are labeled `C_indeterminate`, never
forced into C1–C3.

## Stage B — manual audit of `hash_match` links

Sampling: n=50, stratified 17/17/16 by registration-date tertile, seeded
(`FAULTTAX_SEED=20260828`), cluster = record. If B2–B6 events total <5 at
n=50, extend once by +30 (total 80) and report both estimates
(`cit_acl2026_no_power_analysis`: the field's median is 170 items with no
power analysis anywhere; our n is justified by Wilson-CI width in
`analyze.py`, not convention).

Codes (single most severe per link, one-line justification;
`fault_taxonomy/codebook.md` renders them as a yes/no decision tree per
`cit_ais2023_binary_decomposition`):
B1 supports · B2 overreach · B3 irrelevant · B4 conflict · B5 superseded ·
B6 wrong at registration. B2–B6 constitute the gate's blind spot. B4 uses a
fixed procedure, not wall-clock: one lexical top-10 query over the store
built from the claim's key terms, judged only within those results.

## Stage C — manual audit of `hash_mismatch` links

All if ≤40 after machine triage removes `C1_candidate`s (humans confirm a
seeded 10-item subsample of machine C1s); otherwise 40, same stratification.
Codes: C1 cited range unchanged and still supporting (file-level hash false
alarm) · C2 range changed, claim survives · C3 range changed, claim broken
(true positive) · C4 file deleted/moved · C-indeterminate.

## Reliability

Coder 1: the corpus owner. Coder 2: a Claude session coding 20 seeded
Stage-B links from the codebook alone, blinded to coder 1's labels and to
this campaign's prior per-link judgments (conflict of interest: the same
model family designed this protocol; disclosed here and bounded by the
independent machine census). Threshold: Cohen's κ ≥ 0.6 — inside the band a
peer-reviewed benchmark accepted as validity evidence
(`cit_alce2023_kappa_acceptance`: 0.525–0.698) and at the floor of
Landis-Koch "substantial" (`cit_landiskoch1977_bands`, with its
acknowledged arbitrariness disclosed). If κ < 0.6: revise codebook
definitions, recode, and route remaining disagreements to third-review
adjudication (`cit_ragtruth2024_agreement_adjudication`), whose outcomes
are reported separately.

## Metrics and decision baseline

- Gate visibility (A) = (file_missing + range_invalid + hash_mismatch) /
  links with hash-bound refs; `no_ref`/`ref_without_hash` rates separately.
- Blind-spot rate (B) = (B2..B6)/n with Wilson CI.
- Substantive-mismatch rate (C) = C3 / (C1+C2+C3+C4), with the machine
  C1_candidate population rate reported alongside.
- Addressable = caught true failures / all true failures, where caught ≈
  mismatch rate × substantive rate and missed ≈ match rate × blind-spot
  rate; cluster (record) bootstrap CI. Two preregistered denominators:
  D1 hash-bound links only (the gate's fair score) and D2 all links
  including `no_ref`/`ref_without_hash` (the corpus reality).
- Decision line, fixed ex ante in the style of a coverage constraint
  (`cit_selectivenet2019_coverage_constraint`): the gate's standing cost is
  ~1.4× uncached tokens and a measured 16.7% false-block rate; we prejudge
  that if Addressable(D1) < 30%, file-level hash gating alone is
  insufficient for this corpus and the next build must be either
  range-level hashing (if the machine C1_candidate rate is high) or
  source-ref backfill (if the `no_ref` stratum dominates D2). The 30% line
  is scaled against the ~50% unsupported-statement rate measured in
  deployed systems (`cit_liu2023_verifiability_rates`): the gate must
  address a substantial minority of real failures to justify always-on
  cost. Poisoning-class failures are out of scope by construction
  (`cit_poisonedrag_threatmodel`; measured 6/6 both-arm failure in the
  paired benchmark).

## Threats (pre-stated)

Single corpus; owner as coder 1; model-family COI for coder 2; survivorship
(recent tertile recorded under gate usage → per-tertile reporting is
primary, pooled secondary); paywalled Landis-Koch verified via secondary
sources only. Link-level data stays private (the corpus is private);
published: aggregates, codebook, scripts, κ, and the citations manifest.
