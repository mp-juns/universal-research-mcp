# Governed Multi-Agent Research Specification

## Status and boundary

This document defines the local deterministic policy layer and bounded
research-worker harness. The policy layer itself does not select a model,
invoke a remote service, execute an experiment, append a canonical record, or
rebuild a search index. The harness may call an explicitly configured executor
only after exact scope-receipt, provider, parallelism, and aggregate-budget
checks; those capabilities remain separately authorized.

The policy layer has one central manager and exactly eleven fixed roles. No runtime
may silently add, rename, or activate another role.

## Fixed roster and modes

The seven operational roles are `scope_and_cost_governor`, `retrieval_governor`,
`benchmark_control_auditor`, `analysis_objectivity_auditor`,
`paper_evidence_evaluator`, `correction_executor`, and
`research_memory_maintainer`.

The four critical reviewers are `cold_adversarial_reviewer`,
`substance_reviewer`, `user_alignment_reviewer`, and
`reproducibility_reviewer`. They remain dormant until the `main_result` or
`final_submission` gate.

| Mode | Active roles | Gate |
| --- | --- | --- |
| `lightweight` | scope/cost governor, retrieval governor, analysis objectivity auditor, memory maintainer | none |
| `benchmark` | all seven operational roles | user-approved benchmark work |
| `final_review` | seven operational roles, then all four critical reviewers | `main_result` or `final_submission` |

`core.governance.active_agents` is the executable roster registry. It returns
only operational roles before a final-review gate and all eleven roles afterwards.

## Role separation

Retrieval may search, fetch, inspect, and review but never create a claim. The
three auditors and all critical reviewers may inspect and review but cannot edit
or execute. `correction_executor` may edit only inside an approved task scope
and only in response to a recorded finding. `research_memory_maintainer` may
repair or index derived artifacts but cannot rewrite canonical history.

The task packet supplies allowed paths, sources, actions, evidence boundaries,
success conditions, and stop conditions. The governance validator rejects a
packet that requests action beyond the role's fixed authority, a role inactive
in the selected mode, or an incomplete evidence boundary.

`scope_and_cost_governor` is always active and deterministically checks plan
necessity, declared resource/cost estimates, and each proposed operation against
the packet. It cannot approve spending or execute work. Host visualization is
off by default and needs both explicit user opt-in and task capability scope;
ordinary data-plot generation is a separate permission.

Operations proposed for execution use a closed, typed declarative preflight
schema. The deterministic gate rejects unknown fields and returns hashes for
the exact preflight operation, task packet, and approved scope. Action-specific
tool arguments still require a closed host envelope and a comparison at the
actual tool boundary; the gate does not imply interception of arbitrary host
calls and always reports execution authorization as false. Dispatch requests
and critical batches are deep-copy isolated, sealed
after all authority fields are present, pinned by the host at build time, and
revalidated before export or provider handoff. These SHA-256 receipts detect
drift but do not authenticate the host; host trust or a future signature layer
is still required against a malicious host.

The retained development provider path also uses a runtime-owned, single-use
reservation for the exact stored dispatch artifact. The provider executor
atomically consumes it before transport; direct invocation and replay without a
pending reservation are rejected.

Failure policy resolves field-by-field from task, profile, environment, then
defaults. Every failure stops immediately before retry or downstream work and
creates at least a hashed metadata tombstone, including when full recording is
deferred for a user choice.

Every packet includes both `estimated_cost_usd` and `max_cost_usd`; zero must be
declared rather than inferred from omission. After a passing governor decision,
the deterministic controller issues a tamper-evident receipt bound to every
worker packet and scope hash. Standalone and critical-batch dispatches fail
closed without that receipt.

Agent creation has an earlier, separate gate. Before requesting any governed
agent, the host must show the user one exact disclosure covering the delegation
reason, one bounded task per proposed agent, the total count, the direct
single-agent alternative, minimum/likely/maximum additional tokens and elapsed
minutes, and exact path/network/model/write scope. Every packet must carry that
unchanged disclosure, a common approval reference, and the explicit
`agent_creation` opt-in. Provider-runtime and secure-harness grants bind its
hash and are consumed before the first provider request or worker process. A
changed disclosure or count requires a new approval. These checks govern
Universal execution paths; they do not intercept an unrestricted host's native
subagent tool outside those paths.

## Machine-auditable artifacts

`schemas/research-agent-task.schema.json` defines the task packet. Every worker
returns a `research-agent-decision/1.0` record validated by
`governance.validation.validate_decision`. The older `core.governance` shape is
a compatibility contract only. Findings contain severity, source
locators, impact, bounded corrective action, and confidence.

Decision artifacts are not canonical evidence by themselves. Load-bearing
conclusions must still fetch and verify the referenced source. Execution
adapters must preserve their command and operation history in the append-only
ledger even when the central manager presents only a summary in chat.

## Claim escalation

`core.governance.claim_gate` treats `critical` and `high` findings as blocking
for publication-facing claims. A blocked or inconclusive reviewer result also
cannot be converted into a positive conclusion. The result reports blockers
and requires a user decision; it does not automatically repair, accept risk, or
close any finding.

## Central-manager chat policy

The central manager creates a summary-only report by default. Its permitted
content is status, outcome, material risks, blockers, required user choices,
concise metrics, artifact references, and whether work was executed, reviewed,
indexed, blocked, or merely proposed.

Raw command output, logs, JSONL records, stack traces, prompts, reviewer
reasoning, and large internal tables remain in the audit record unless the user
asks for that particular material. `user_chat_report` marks this as
`summary_only` or `user_requested_detail`; this presentation choice never
changes the append-only research record.

## Derived-index refresh policy

`research_memory_maintainer` may refresh only derived lexical or semantic
search artifacts after a canonical research event is recorded. The policy
recognizes decisions, execution outcomes, observations, claims, amendments,
audit findings, negative results, stopped work, and artifact/provenance changes
as retrieval-relevant events. It does not refresh from unrecorded proposals,
format-only changes, raw chat, private prompts, secrets, or an indiscriminate
repository crawl.

`core.index_refresh.refresh_eligibility` merely returns whether a recorded event
may trigger a refresh. `schemas/index-health.schema.json` and
`validate_index_health_record` require an index revision, source event IDs,
model/version metadata, hashes, failure state, and a retrieval verification. A
refresh cannot report success without a passed retrieval check. On failure, the
canonical event remains intact and the derived index is reported as partial,
failed, or stale.
