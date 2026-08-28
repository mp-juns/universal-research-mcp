# Hash-Bound Evidence Gating for Research Agents: Enforcement Is Easy, Activation Is the Problem

*Draft v1 — 2026-08-28. Working paper for the Universal Research MCP project.
All numbers trace to preregistered protocols, committed analysis code, and
aggregate result files in `benchmarks/`; raw trial streams remain local.*

## Abstract

Research agents that answer from long-lived project memory routinely assert
values whose underlying evidence has changed, been withdrawn, or was never
registered. We present and evaluate a fail-closed evidence-eligibility gate
for a canonical append-only research ledger: claims must cite fetched
evidence whose file hash still matches its registration. Across two
preregistered paired benchmarks (144 synthetic-corpus and 162 real-corpus
runs), the gated workflow eliminated unsafe assertions on hash-detectable
faults (21–22/45 → 0/45; 23–26/27 → 0/27; risk differences 0.49 and 0.85
with 95% CIs excluding zero) at unchanged clean coverage. We then audit the
mechanism rather than celebrate it. A 686-link census of a real eight-month
ledger shows the gate can bind only 12.6% of evidence links, 96.7% of its
alarms are range-level false positives (the cited content is byte-identical,
merely moved), and hash-passing evidence still carries a 7.6% claim-level
blind spot; a preregistered decision rule concludes file-level hash gating
alone is insufficient, and a retrospective replay validates content-anchored
(passage-hash) verification that removes 59/61 false alarms while retaining
both true ones. Finally, a preregistered ablation removes the workflow
scaffold: with the MCP server attached but nothing prompting its use, the
model made zero MCP calls in 72/72 runs and the gate arm was exactly as
unsafe as plain file access (RD 0.000), while a one-line prompt plus a hash
manifest recovered only the hash-visible faults (10/45 unsafe). Activation,
not enforcement, is the deployment problem: a repository policy file that
both mandates the workflow and pre-approves session scope restores full MCP
engagement, whereas product-side tool-description triggers alone do not (0/45
activation) — and even activated runs reveal a third layer: models
cherry-pick intact evidence around visibly mismatched sources (12/42
activated runs). A server-side disclosure of the omission is ignored
(9/13 overridden); the fail-closed version drives integrity-omission
failures to zero (4/45 total unsafe, zero false blocks). The measured
ordering — information < instruction < enforcement — held at every
layer. We contribute the benchmark suite, the fault
taxonomy with reliability analysis, and the design consequences.

## 1. Introduction

Agent memory systems promise continuity across sessions; they also create a
new failure class: **confidently asserting recorded values whose evidentiary
basis no longer holds**. In an actively developed research project, files
drift, indexes go stale, claims are withdrawn, and sources are cited without
registration. A benchmark-driven agent that reads such memory directly will
repeat whatever it finds.

Universal Research MCP binds evidence at registration time: every canonical
event may carry `source_refs` with path, line range, and SHA-256; a claim
gate (`memory_check_evidence_eligibility`) refuses eligibility when the
chain is broken or the hash no longer matches. This paper asks four
questions in sequence:

1. Does the gated workflow prevent unsafe assertions when faults are
   hash-detectable? (§5: yes, to zero, in both corpora.)
2. What fraction of real faults is that mechanism even positioned to catch,
   and how precise are its alarms? (§6: 12.6% visibility; 3.3% of alarms
   substantive; preregistered conclusion: file-level hashing alone is
   insufficient; content anchoring is the measured fix.)
3. Do the headline results survive removing the workflow scaffold and
   adding a cheap baseline? (§7: no — activation collapses to zero and the
   scaffold is revealed as the treatment; a hash manifest closes less than
   half the gap.)
4. Can deployable artifacts recover activation? (§7.3: a repository policy
   file does; tool-schema triggers alone do not [v1.1].)

The honest synthesis is not "the gate makes agents safe" but: **enforcement
is real and conditionally perfect; adoption is zero without workflow
policy; and the alarm itself needs content anchoring to be trustworthy at
range level.** We believe this three-part shape — mechanism, mechanism
audit, adoption audit — is the right template for evaluating agent-memory
safety tooling generally.

## 2. System under test

Universal Research MCP stores research events as append-only JSONL under
`research-events/daily/`, with a source registry (`sources.jsonl`) binding
paths to SHA-256 digests, derived SQLite FTS5 lexical and optional semantic
indexes, HMAC one-time receipts for canonical ingestion, and a claim gate
whose plan is claim-type aware (result/comparative/causal/release claims
are auto-material; two-source rules for comparative/causal/release). The
serving surface is 30+ MCP tools; the multi-agent governance layer (11
fixed roles) ships alongside but has **zero benchmark coverage here** and
is explicitly out of scope (§9).

## 3. Fault model

Evidence states exercised across the studies: `post_index_mutation`,
`line_range_drift`, `stale_derived_index`, `path_reuse`,
`withdrawn_evidence`, `missing_evidence`, `unregistered_source`,
`conflicting_evidence`, `valid_but_irrelevant`, plus two negative controls
designed to be uncatchable: `poisoned_before_registration` (corruption
precedes registration) and verified-but-stale (an intact hash admits an
outdated value). Real-corpus faults are natural: nothing was mutated to
create them.

## 4. Statistical methodology (preregistered, literature-grounded)

Single proportions use Wilson score intervals; risk differences use
Newcombe hybrid score intervals plus task-cluster bootstrap CIs; paired
tests use mid-p McNemar — the exact conditional test used in our earliest
reports is abandoned as uniformly dominated; zero cells additionally report
the rule-of-three bound 3/n. Every methodological choice is bound to a
verbatim, hash-verified quotation from its source paper in the citation
manifests (`benchmarks/protocols/*.citations.json`), each quote registered
as a canonical event in a RAG store with an eligibility probe — the paper's
own methods are dogfooded through the system it studies.

## 5. Paired benchmarks: enforcement works when invoked

**S1 Synthetic (24 tasks × 2 arms × 3 reps = 144 runs; gpt-5.6-sol,
medium):** unsafe assertions on the 45-run hash-detectable fault stratum:
filesystem 21/45 (blinded) / 22/45 (deterministic) vs gated **0/45**;
RD 0.489 [Newcombe 0.329, 0.630; bootstrap 0.267, 0.711]; clean coverage
21/21 in both arms; zero false blocks.

**S2 Real corpus (27 tasks × 2 × 3 = 162 runs on an eight-month, actively
dirty research project):** natural-fault stratum 26/27 (blinded) / 23/27
(det) vs **0/27**; RD 0.852 [0.636, 0.941]; false blocks 9/54, all traced
to legacy events registered without source references — evidence-chain
quality, not the gate, bounds utility. Negative controls held in both
corpora: pre-registration poisoning defeated both arms (6/6 each);
verified-but-stale defeated the gate arm 3/3.

**S3 Cost:** payload −49%, uncached tokens −10% after a same-day
optimization pass with fault-unsafe still zero. Tool-call medians (trace
audit): real-fault gate arm 19 calls (IQR 7–33, max 74) vs filesystem 6
(4–8) — the gate's real cost is retrieval effort, not blocking.

**Judge validity:** verdicts came from a condition-blinded LLM evaluator
cross-checked by an independent deterministic scorer; a 50-verdict blind
re-rating by two independent raters agreed with each other κ = 1.000 and
with the blinded judge κ = 0.865 each (the two disagreements are a
documented stratum-semantics difference, not perception).

## 6. Fault taxonomy: what the gate can and cannot see

Exhaustive census of all 686 (record, source_ref) links in the real ledger
(zero model tokens): hash_match 66.6%, hash_mismatch 8.9%,
ref_without_hash 19.4%, no_ref 2.9%, unrecoverable 1.3%, range_invalid
0.9%. **Gate visibility D1 = 12.6%**; broken chains (never bindable) =
23.6%, with hash-less registration collapsing in the corpus's final
tertile — the measured false-block cause, located in time.

Machine decomposition of all 61 mismatches (slice-equality predicate,
corrected mid-study from a diff-overlap predicate that a preregistered
seeded confirmation caught at 5/10): 32 slice-identical (C1), 27
displaced-intact (C2m), **2 substantive (C3)** — 96.7% of alarms fire on
byte-identical cited content.

Stage-B audit of 80 hash-passing links (79 rated, one B0): blind spot
**6/79 = 7.6%** [3.5%, 15.6%], all partial-support overreach. Two-coder
reliability: raw 15/20 with Cohen κ = 0.000 — a textbook kappa paradox
under a constant marginal — so **Gwet AC1 = 0.729** is reported as the
honest estimate; residuals were adjudicated on evidence with the owner's
pass deferred at the owner's direction, and one coder error (a claim
component outside its registered range) was flipped against ourselves.

Preregistered decision: Addressable(D1) = 5.5% [2.7%, 11.0%] < 30%
baseline → **`hash_gate_alone_insufficient`**, CI-robust. Design
consequence, then measured: a retrospective replay of content-anchored
(passage-hash) verification over the same 686 links removes exactly the
59 non-substantive alarms and keeps both substantive ones, flipping no
passing link (ADR 0004). This reattributes the paired-benchmark safety
wins to the workflow's abstain-on-alarm discipline: the alarm is
imprecise at range level, but stopping on it was the safe behavior.

## 7. Ablation: the scaffold is the treatment

Preregistered before any run (protocol + analysis code committed; frozen
task/scorer hashes): 3 arms × 24 × 3 = 216 runs with **no scaffold** — no
claim types, no scope preamble, no tool naming in any prompt.

| arm | fault unsafe (45) | RD vs filesystem | clean |
| --- | ---: | --- | ---: |
| filesystem | 24/45 | — | 21/21 |
| mcp_natural | 24/45 | **0.000** [bootstrap 0.000, 0.000] | 21/21 |
| manifest_prompt | 10/45 | 0.311 [0.111, 0.479], mid-p 7.3×10⁻⁴ | 21/21 |

### 7.1 Activation zero

The natural arm made **zero MCP calls of any kind in all 72 runs** —
gate, search, and fetch untouched — despite a session probe confirming
all 30 urtrial tools were exposed (amid 150+ unrelated account-level
tools, an ecologically realistic distractor field). Effective protection
= activation × enforcement = 0. The scaffolded 0/45 of §5 is therefore a
*conditional* result, and every safety claim in this paper is conditioned
on activation.

### 7.2 The cheap baseline

A one-line instruction plus a registration-time hash manifest fixed
precisely the faults a hash can see (post-index mutation 8→1, line drift
6→1) and none it cannot (stale index, valid-but-irrelevant), with clean
coverage fully protected — above our preregistered 20% bar, so it does
not close the gap, but it halves risk at near-zero engineering cost.
Under equal scaffolding budgets the enforced gate still dominates DIY
hash checking (0/45 vs 10/45). Disclosure: the original development-study
manifest arm was broken by construction (its manifest hashed post-fault
files); v1 corrected this. A fixture-sharing defect also exposed the v1
natural arm to the manifest file; the direction of bias is conservative
(the extra integrity information did not reduce natural-arm unsafe), and
a clean rerun replaces the headline number [v1.1].

### 7.3 Prescriptions for activation [v1.1 — numbers pending]

Two deployable levers, preregistered as amendment v1.1, each 24 × 3:

- **Repository policy file (`AGENTS.md`).** A pilot produced a mechanism
  finding first: pointing the model at the server made it obey the
  server's shipped ASK-FIRST scope instruction and stall single-turn
  sessions — proving server instructions are delivered (the natural arm
  ignored the server wholesale) and that the scope gate blocks
  non-interactive use unless scope is preapproved. With one
  owner-preapproval paragraph added, activation recovered to **42/45
  (93%)** with shell abandoned (~8 MCP calls/run, +85% tokens) and unsafe
  halved to **12/45** (RD vs filesystem 0.267 [0.064, 0.441], mid-p
  2.4×10⁻⁴; clean 21/21) — yet it fails our preregistered validation bar
  (unsafe ≤ 2), for a newly measured reason below.
- **Tool-description triggers (product-only).** A dead lever: verifiably
  delivered in the tool schema, **0/45 activation**, 25/45 unsafe —
  indistinguishable from no MCP at all.
- **The clean natural rerun** reproduced the contaminated v1 number
  exactly (24/45, zero MCP calls), retiring the contamination concern.

### 7.4 Evidence selection: the third protection layer

All 12 unsafe activated runs held **eligible** receipts — no block was
overridden. In 9/12 the model fetched the faulted source, saw the
integrity mismatch in the fetch result, silently dropped it, and
submitted only intact evidence to the gate, which correctly ruled the
cited subset eligible; 3/12 never fetched the faulted source. The gate
verifies what you cite, not what you omit; the original scaffold's "pass
each returned reference unchanged" was itself a protection layer.
Protection therefore decomposes into three separately measured factors —
**enforcement × activation × citation discipline**. We then built and
measured the third layer server-side in two preregistered steps. A
*disclosure* receipt (naming each fetched-but-uncited mismatched
reference, with an instruction to abstain or address it) fired with
perfect precision (13/45 fault runs, 0/21 clean, 0/6 negative-control) —
and was overridden in 9/13, leaving unsafe unchanged (11/45, mid-p
0.625): the preregistered falsification rule triggered. A *fail-closed*
variant (active material claims return `blocked:
OMITTED-MISMATCHED-EVIDENCE`; citing the mismatched reference lifts the
block) met every preregistered bar: unsafe **4/45** (RD vs AGENTS.md
baseline 0.178, mid-p 0.022), 0/12 unsafe where the block fired, zero
clean false blocks, clean coverage intact. The four residual failures
are intact-hash semantic states (withdrawn, irrelevant) outside the
integrity gate's documented remit. Across both layers the same ordering
repeated — **information < instruction < enforcement**: tool schemas,
in-context hash manifests, and disclosure receipts changed nothing;
policy instructions recovered activation; only enforcement drove a
failure class to zero. The first honest claim-type self-report
measurement (77 unprescribed gate calls) shows a 9.1% downgrade rate,
all evading the two-source `causal` rule.

## 8. Related work

Verifiability of generated citations (Liu et al. 2023) motivates the
external scale for our blind-spot magnitude; RAG poisoning threat models
(PoisonedRAG) ground the poisoned-before-registration control; selective
prediction (SelectiveNet) grounds coverage-constrained abstention; AIS
binary decomposition and RAGTruth agreement/adjudication ground the
codebook design; Landis-Koch bands and the kappa-paradox literature ground
the reliability reporting; FreshQA change-rate stratification grounds the
census tertiles; MetaTool grounds activation as a first-class measured
capability; "AI Agents That Matter" grounds cost co-reporting and the
cheap-baseline arm; MT-Bench grounds LLM-judge validation; Newcombe,
Fagerland, and the rule-of-three ground the statistics. Every grounding is
a hash-verified verbatim quote in the citation manifests.

## 9. Limitations and threats

Single model family (gpt-5.6-sol/medium) and one real corpus; blinded
judging by an LLM (validated at κ = 0.865 against two independent raters,
but not human-adjudicated); the governance/roles surface and the
ingest-receipt path under adversarial use are unmeasured; the live plugin
install predates every fix measured here, so end-to-end validation of the
content-anchoring prescription is future work; deterministic scoring
detects planted-value assertions and cannot see all free-text unsafety
(the blinded judge caught 3 real-corpus assertions it missed); private
corpus text stays local, so real-corpus tasks are reproducible in
aggregate only. The owner's independent coding pass for the taxonomy was
deferred at the owner's direction and remains open.

## 10. Conclusion

A hash-bound eligibility gate reduces unsafe assertions to zero *when the
workflow runs* — and does not run by itself. The measured deployment unit
is gate + workflow policy (a repository policy file suffices; tool schemas
alone do not), the measured evidence unit should be the passage hash, not
the file hash, and the honest headline for agent-memory safety tools is
conditional: enforcement × activation, each measured separately.
