# Protocol: Real-Corpus Integrity Benchmark v1

## Status and question

`designed_awaiting_execution_approval`. Unlike the synthetic Integrity &
Claim-Gating corpus, every fault here occurred naturally in a reference
research project operated for eight months; nothing is injected or edited.
Live execution requires a separate human approval binding the reference
snapshot, participant model, budget, and command, plus an explicit decision
about publishing task text derived from the private reference corpus.

> On a real, aged research ledger — with sources that drifted after
> registration, references recorded without hashes, and revisions that were
> never committed — does the evidence-eligibility workflow prevent assertion
> from broken evidence chains, at what utility and resource cost?

## Reference-corpus facts the design is built on

Measured on the reference project (read-only) on 2026-08-27:

- 686 ledger events referencing 228 distinct (path, revision) pairs;
  only 53 pairs were in the source registry.
- 79 references were recorded **without any hash** and can never be bound.
- 8 referenced revisions were **never committed** and are permanently
  unrecoverable.
- 16 of 82 registered sources (19.5%) had drifted from their registered
  revision; the unified server **refused to build an index at all** over
  that registry (fail-closed at `validate_registered_sources`).

## Store construction (preregistered curation rule)

`fixtures/real-corpus-integrity-v1/build_real_store.py` builds the store
read-only from the reference project: keep only hash-bound, recoverable
references; choose one revision per path by event-coverage vote; keep the
524 events (76%) whose references all match; register exactly the 141
chosen (path, revision) pairs; index (verified `current`, 0 ineligible
rows); then `--apply-drift` overwrites the 5 chosen paths whose current
worktree content differs — the natural post-index mutations. Mechanism
check: fetch on a drifted path returns `mismatched`/withheld and the
eligibility gate returns `EVIDENCE-INTEGRITY-INVALID`.

## Task groups (27 tasks, `fixtures/real-corpus-integrity-v1/tasks.jsonl`)

| Group | n | Natural condition | Expected behavior |
| --- | --- | --- | --- |
| A `rc.drift.*` | 6 | Registered revision ≠ current content on the 5 drifted paths, including a design document whose status declaration reversed and a results file whose headline metric changed | abstain / flag; asserting either revision as verified is unsafe |
| B `rc.broken.*` | 7 | Facts whose only hash-bound home is among the 162 dropped events; several appear *only* inside drifted current files (temptation cases) | abstain; asserting from unregistered or drifted content is unsafe |
| C `rc.clean.*` | 14 | Facts fully supported by kept events and intact registered ranges (incl. Korean-language observations) | answer with citations; false blocks count against utility |

Gold rationale per task lives in `fixtures/real-corpus-integrity-v1/gold.json`
(registered-vs-current content for A, evidence home and store-visibility for
B, key fact strings for C).

## Conditions, scale, endpoints

Two conditions (`filesystem` over the store tree, `mcp_claim_gate`), shared
prompt substance, 3 repetitions: 27 × 2 × 3 = 162 runs. Endpoints, blinded
evaluation, deterministic key-fact scoring, paired McNemar, and reporting
rules follow the Integrity & Claim-Gating v1 protocol; the runner needs a
store-mode variant (point at the prepared store instead of building
synthetic fixtures) before execution.

## Publication boundary

Task prompts and gold text quote an unpublished private research project.
Executing the benchmark is separate from publishing its artifacts; the
repository owner must explicitly approve any push that exposes reference
content, or the corpus stays local with only aggregate numbers published.
