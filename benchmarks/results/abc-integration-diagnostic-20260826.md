# A/B/C integration diagnostic — 2026-08-26

## Status and scope

Completed source-checkout integration diagnostic, **not a uniform-budget
benchmark, released-package efficacy measurement or confirmatory result**.
`gpt-5.6-terra` (rolling alias), medium reasoning effort, produced one tool-backed
response per condition to the same public synthetic task, `abc2auto.t001`.
There were no repetitions, human evaluators or LLM judges. Scoring was the
existing deterministic exact rubric; no answer or failed score was repaired.

The three tool-backed responses followed seven preliminary startup diagnostics.
All **ten actual Codex processes** are represented in the
[sanitized machine-readable projection](abc-integration-diagnostic-20260826.json),
including four attempts with unknown usage. Provider-internal request count is
unavailable. This report is separate from the earlier 28-run pilot and is not a
full 432-trial result. No in-progress experiment results are included.

## Observed responses

This development task had sufficient current evidence and expected a supported
answer with precise source references. A is filesystem access, B adds MCP
evidence retrieval, and C additionally exposes evidence eligibility checking.

| Condition | Successful tool calls | Observed decision | Strict automatic score | Retained execution outcome |
| --- | ---: | --- | ---: | --- |
| A: filesystem | 6 | `supported` | 0/1 | Stopped after exceeding its original 64,000-input ceiling; answer retained |
| B: MCP evidence-only | 5 | `not_supportable` / `abstain` | 0/1 | Completed integration; incorrect abstention on this supported task |
| C: MCP + evidence eligibility | 5 | `supported` | 1/1 | Completed integration and exact fixture rubric |

A made the expected supported decision but cited extra ledger/index material,
so exact evidence precision failed (two gold references among four submitted).
B retrieved both expected source references but abstained. C submitted the two
expected references and obtained an eligibility receipt. This does not mean
the receipt proved semantic truth: it checked the source identity, registered
revision, range and evidence-count boundary.

**Execution and answer quality are distinct.** A's retained answer can be
scored even though its run stopped; B completed the integration but failed the
answer rubric. C's 1/1 is one observed task, not a superiority estimate.

## Usage, time and normalization

| Condition | Input ceiling | Reported input | Cached input subset | Reported output | Recorded latency | Normalized list cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 64,000 | 66,378 | 52,480 | 626 | 27.081 s | $0.045804 |
| B | 128,000 | 51,226 | 30,720 | 620 | 23.712 s | $0.054596 |
| C | 128,000 | 51,760 | 26,368 | 647 | 27.128 s | $0.0638216 |

A's original ceiling violation is preserved. After it, B/C used a revised
diagnostic ceiling, making the budgets heterogeneous even though their observed
input counts were below 64,000. These rows must not be presented as a fair
uniform-budget performance comparison.

For transparent historical normalization only, the accounting uses fixed rates
per million tokens: input $2, cached input $0.20, output $12. Cached input is a
subset of total input, not an additional input charge:

```text
normalized USD = ((input - cached_input) * 2 + cached_input * 0.20 + output * 12) / 1,000,000
```

This is **not current pricing or actual provider billing**. The three rows sum
to $0.1642216. All six attempts with known usage sum to $0.2099096. Four unknown-
usage attempts remain null; their separate conservative accounting reservation
was $0.708608, giving $0.9185176 known-plus-reserved. A reservation is not
measured usage or proof that actual billing stayed below any ceiling.

## Failure retention and provenance

The seven preliminary attempts remain listed with original result hashes,
recorded diagnostics/stop flags, known or unknown usage and tool-receipt counts.
They involved schema/tool-routing startup diagnosis; they are not extra
successful task trials or repetitions. The three response rows are the tool-
backed end points of that diagnostic sequence, not a standalone 3/3 success rate.

Before publication, the original integration report and all ten retained result
self-hashes were checked locally. The unchanged task/evaluator source hashes,
archived source patch/helper hashes, three exact gold evaluations and completed
behavioral analysis were also checked. Original report hash:

```text
sha256:e674981a120d1e1448e2c18b3b73aabb3e6a9acacebb7e77a7adab3f7f94e942
```

Public projection hash:

```text
sha256:787a3028904180d75379b8bb99b8892e8c39fb8b07aaf9d45c71c4e92029ad60
```

The projection deliberately omits session/work logs, local execution paths,
prompts, executable authority records and raw model streams. It is **not the
original report**, and its new hash must not be confused with the preserved
original hashes. The private archives are not included; discarded raw streams
cannot be reconstructed or independently replay-verified from these hashes.
This is not an attested production approval/resume execution.

## Interpretation boundary

The diagnostic establishes that tool-backed structured responses were observed
for all three interfaces, with a preserved budget stop and mixed answer quality.
It does not establish general hallucination reduction, causal product benefit,
research-quality improvement, provider/model superiority or statistical
significance. One public task, unequal budgets, a changing diagnostic setup,
rolling model alias and absent independent human review limit interpretation.

See the [completed-results index](README.md) for other completed studies; their
observations and denominators are deliberately not pooled with this diagnostic.
