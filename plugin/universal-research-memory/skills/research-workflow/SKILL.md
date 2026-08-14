---
name: research-workflow
description: Use the Universal Research Memory MCP to retrieve research history with DB-first candidate search, source verification, explicit uncertainty, and no unapproved writes.
---

# Universal Research Workflow

Use this skill for questions about previous research work, plans, protocols,
decisions, results, failures, or artifacts managed by this project.

If `research_profile_status` reports a profile, use its declared retrieval mode
only after the corresponding local derived index is current. The profile can
declare code, build-definition, configuration, and documentation source scope,
but it does not register files or make them evidence-eligible. Never treat a
profile's provider route or registered Skill ID as permission to call an API,
read a secret, create a Skill, or start a subagent.

## Evidence-first retrieval

1. Call `memory_search_candidates` to obtain candidate records.
2. Treat every returned score, preview, and summary as candidate metadata.
3. For any material claim, call `memory_fetch_evidence` with the returned
   `event_id`, path, line range, and `source_sha256` as `expected_sha256`.
4. Before reporting a material result, comparison, causal statement, release
   decision, or other load-bearing fact, pass each evidence fetch response's
   `claim_gate_reference` object unchanged to `memory_gate_claim`. Use
   `materiality="material"` for a load-bearing factual statement. Do not
   substitute the fetch response's current `sha256` for `expected_sha256`.
5. Do not state a claim when the gate returns `blocked`; state the blocker and
   uncertainty instead. A routine lookup does not need the gate.
6. State the verified path, line range, and uncertainty with the conclusion.
7. If evidence conflicts, preserve the conflict rather than merging it.

For ordinary search, use the default configured mode so the MCP resolves the
explicit project profile. An `adaptive` profile chooses lexical retrieval only
for clear file paths, flags, and code identifiers; otherwise it attempts the
configured semantic view. Inspect the returned `routing` field: a lexical
fallback is not a semantic result and remains candidate-only.

For a recency question, call `memory_latest` before ordinary search.

For an operating-policy, provenance, or approval review, call
`memory_audit_ledger`. Treat its findings as review requests; it does not
approve a session, alter a record, or settle a scientific conclusion.

## Operating boundaries

- The configured automatic lexical bootstrap may create or replace only the
  project-local derived index after staging verification. Do not otherwise
  create, modify, delete, copy, index, test, install, network, call a model, or
  run background work without explicit user approval and scope validation.
- Do not treat semantic similarity or search rank as research evidence.
- Do not make unsupported faster, better, optimal, causal, or performance
  claims.
- Keep Expected, Observed, Interpretation, and Uncertainty separate.
- Do not turn a draft proposal into a canonical record without an explicit
  approval and append-only write workflow.

## Results format

When reporting a research conclusion, use this order:

```text
Observed: verified source/artifact fact
Interpretation: bounded inference, if any
Uncertainty: limitation, conflict, or missing verification
Evidence: path and line range
```
