---
name: research-workflow
description: Use the Universal Research Memory MCP to retrieve research history with DB-first candidate search, source verification, explicit uncertainty, and no unapproved writes.
---

# Universal Research Workflow

## Mandatory session scope confirmation

Before research tools or any task execution in EVERY NEW session, ask the user
to confirm the current workspace and permission scope, then WAIT for their
explicit reply. Apply [session-scope.md](../../hooks/session-scope.md): host
shell; ordinary scoped file creation/editing allowed after confirmation;
external network/download approval BEFORE EACH operation; ZERO agents.
Ask even if the initial task sounds actionable. Defaults and a previous
session's approval never authorize this session. Preserve an explicit
same-session confirmation after resume/compaction; ask again if uncertain.

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
   `claim_gate_reference` object unchanged to
   `memory_check_evidence_eligibility`. Use
   `materiality="material"` for a load-bearing factual statement. Do not
   substitute the fetch response's current `sha256` for `expected_sha256`.
5. Treat `eligible` only as integrity/range/count eligibility. It does not
   establish semantic support, resolve conflicts, or prove the source true.
6. Do not state a claim when eligibility is `blocked`. When it is eligible,
   review relevance and conflicts before stating a bounded conclusion.
7. State the verified path, line range, and uncertainty with the conclusion.
8. If evidence conflicts, preserve the conflict rather than merging it.

For ordinary search, use the default configured mode so the MCP resolves the
explicit project profile and candidate backend. `event_first` is only an
alternative candidate-ordering/fusion policy over Universal's current derived
indexes; it does not authorize access to a predecessor database or watcher.
Require `routing.identity_gate.status=passed` before fetching any candidate.
An `adaptive` profile chooses lexical retrieval only
for clear file paths, flags, and code identifiers; otherwise it attempts the
configured semantic view. Inspect the returned `routing` field: a lexical
fallback is not a semantic result and remains candidate-only.

For a recency question, call `memory_latest` before ordinary search.

For an operating-policy, provenance, or approval review, call
`memory_audit_ledger`. Treat its findings as review requests; it does not
approve a session, alter a record, or settle a scientific conclusion.

## Operating boundaries

- The bundled launcher does not auto-refresh indexes before session approval.
  After confirmation, ordinary host-shell work and file creation/editing may
  proceed only within the confirmed task/workspace scope. Index setup must be
  an explicit in-scope operation and may update only project-local derived
  state. Destructive actions, canonical writes, model runs and background work
  retain their separate approval gates.
- Ask for explicit approval before EACH external network or download/install
  operation, even if a command prefix or automatic host reviewer would allow
  it. Disclose destination/source, purpose, local write scope and material cost.
- Do not treat semantic similarity or search rank as research evidence.
- Do not make unsupported faster, better, optimal, causal, or performance
  claims.
- Keep Expected, Observed, Interpretation, and Uncertainty separate.
- Do not turn a draft proposal into a canonical record without an explicit
  approval and append-only write workflow.
- Do not start an agent or subagent from this retrieval workflow. If delegation
  becomes materially necessary, switch to the research-governance workflow,
  disclose the reason, tasks, count, direct alternative, token/time ranges, and
  exact scope, then wait for explicit approval of that exact disclosure.

## Results format

When reporting a research conclusion, use this order:

```text
Observed: verified source/artifact fact
Interpretation: bounded inference, if any
Uncertainty: limitation, conflict, or missing verification
Evidence: path and line range
```
