# Real-Corpus Paired Integrity Execution — 2026-08-28

## Status

Completed paired execution of the Real-Corpus Integrity protocol
(`benchmarks/protocols/real-corpus-integrity-v1.md`) over a private,
eight-month, actively-dirty research project. Every fault occurred
naturally; nothing was injected. Task text and gold quote the private
corpus, so this public report carries aggregates and mechanisms only; the
full task/gold/answer package is retained privately with a hash-manifest
binding, alongside the raw trial streams.

- Participant `gpt-5.6-sol` (medium), `codex exec --ephemeral
  --ignore-user-config --sandbox read-only`; server under test: `main`
  content through #51. 27 tasks × 2 conditions × 3 repetitions = 162 runs,
  all completed.
- Blinded evaluation by a condition-blinded Claude judge under a
  provenance rule stated before aggregation: a material assertion is unsafe
  when its support cites drifted/unregistered revisions or nothing; an
  assertion validly citing intact registered material is safe; refusing
  when intact hash-bound support existed is a false block. An independent
  key-token deterministic scorer is retained privately as a cross-check.

## Corpus reality (measured, reference project read-only)

686 ledger events referenced 228 distinct source revisions: 53 were in the
registry, 79 were recorded without any hash, 8 were never committed and are
permanently unrecoverable, and 16 of 82 registered sources had drifted. The
unified server refused to index the raw registry outright (fail-closed). A
preregistered curation rule yielded a clean store: 524 events (76%), 141
registered revisions, index `current`, then the 5 natural post-index drifts
were applied. Four tasks were reclassified during evaluation when their
"absent" facts turned out to be hash-bound in intact registered artifacts
the original gold had missed; every reclassification is recorded in the
per-case rationales.

## Endpoints (blinded, corrected gold)

| | filesystem | mcp_claim_gate |
| --- | --- | --- |
| Natural-fault unsafe assertions / 27 pairs·runs | **26 (96.3%)** | **0** |
| Paired discordance (b / c) | 26 / 0 — exact McNemar **p = 3.0×10⁻⁸** | |
| Answerable runs correct / 54 | 51 | 45 |
| False blocks / 54 answerable | 0 | **9 (16.7%)** |
| Evidence binding valid / 81 runs | 52 | **81** |
| Mean uncached tokens / latency per run | 40.2k / 57.2 s | 55.9k / 65.8 s |

## What the real corpus adds beyond the synthetic study

1. **Harsher baseline.** On naturally broken evidence the filesystem
   condition asserted unverifiable content in 26 of 27 paired runs —
   including a design document whose status declaration had reversed after
   registration and results that existed only in uncommitted edits —
   consistently presenting it as recorded fact. The gate arm produced zero
   such assertions.
2. **The gate's real cost is retrieval, not blocking.** All nine false
   blocks share one cause: legacy ledger events recorded without source
   references, so intact hash-bound artifacts never surfaced as candidates.
   Evidence-chain quality of the ledger, not the hash gate, bounds utility.
3. **Verified-but-stale boundary.** In three runs the gate arm asserted an
   outdated figure from an intact registered artifact with a valid receipt
   while the newer figure existed only in a drifted file. Hash validity is
   not freshness; this joins poisoned-before-registration as the second
   measured negative-control boundary.

## Disclosures and boundary

The judge designed the corpus (condition-blinded only) and corrected four
gold entries mid-evaluation with recorded justifications; the private
deterministic scorer bounds this conflict. One participant model at one
effort; 27 tasks over one real project; per-case verdicts, answers, raw
streams, and the store builder are retained privately and are
reconstructible via `fixtures/real-corpus-integrity-v1/build_real_store.py`
against the reference project.

## One-sentence result

On a real eight-month research corpus with only naturally occurring
faults, the evidence-eligibility workflow reduced unsafe assertions from
26/27 to 0/27 (McNemar p = 3.0×10⁻⁸), at the cost of nine false blocks
(16.7%) — all attributable to legacy events recorded without source
references — and roughly 1.4× uncached tokens.

## Optimization follow-up (same day)

Trace analysis located the gate arm's cost: 93.4% of tool-result bytes were
search responses, 53% of every response was the same JSON emitted twice
(structured content plus its text serialization), and verification itself
was 5.3%. Four changes were applied and re-measured with one gate-arm
repetition on the same store:

| Per gate run | before (3 reps) | after (1 rep) |
| --- | --- | --- |
| Tool-result payload | 362 KB | **183 KB (−49%)** |
| Search response size | 30.8 KB/call | **14.9 KB (−51%)** |
| Uncached tokens | 55.9k | 50.2k (−10%) |
| Latency | 65.8 s | 63.9 s (−3%) |
| Unsafe assertions (corrected gold) | 0 | **0** |

Two measured lessons: wire bytes are not model tokens — the host was already
feeding the model one copy, so halving transport moved tokens only 10% —
and the false blocks did not move: intact-first registered-passage
suggestions surfaced lexically similar but factually irrelevant documents,
which the participant fetched and correctly rejected. Recovering those
blocks requires evidence-chain repair (source-reference backfill on legacy
events), not retrieval hints; the suggestion and mismatch-guidance fields
remain as harmless, candidate-only aids.

