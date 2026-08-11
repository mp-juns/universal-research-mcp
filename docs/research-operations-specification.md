# Universal Research Operations Framework Specification

## 1. Purpose and non-goals

Universal Research Memory MCP does not embed knowledge of a particular model,
research field, or paper. Its purpose is to operate research work by means of
**provenance-bearing, append-only records** and to keep search and automation
under that evidence hierarchy.

It covers research plans, protocols, approvals, execution sessions, raw and
derived artifacts, revisions and lineage, Expected/Observed/Interpretation/
Uncertainty separation, claims and their evidence, human/AI/external-system
contributions, amendments, negative results, stopped work, reproducibility
fingerprints, and safely extensible domain/project profiles.

It deliberately does not use semantic similarity to establish facts, causation,
or performance; expose approval, canonical-ledger writes, or amendments as MCP
tools; or expose model loading, benchmarks, daemons, or remote proxies from the
default MCP. It never treats reference-project measurements as universal rules.

## 2. Layered architecture

```text
Universal Research Core
  ├─ governance vocabulary, record contracts, and provenance validation
  └─ core-to-index projection
       ↓
Study-Type / Domain Packs (additional restrictions only)
       ↓
Project Profile (paths, adapters, reference boundary)
       ↓
Approved execution session
       ↓
Storage / search / display adapters
  ├─ canonical JSONL
  ├─ SQLite FTS and optional semantic index
  └─ Markdown or other display projection
       ↓
Read-only MCP and Codex Skill
```

The core has no dependency on a particular directory name. `TODO.md` and
`WORK_LOG.md` are display adapters, JSONL is the canonical event-stream
backend, and SQLite/vector indexes are derived retrieval views.

## 3. Authority and provenance

Interpret authority in this order:

1. append-only canonical JSONL record;
2. original artifact and the revision hash referenced by that record;
3. lexical SQLite and optional semantic index;
4. MCP candidate search; and
5. Markdown TODO, work log, or session note.

Indexes and Markdown may be rebuilt or changed; they never replace canonical
records. A load-bearing conclusion requires the original artifact path,
line/page/row range, and current hash.

```text
canonical JSONL → derived-index candidate → source-range fetch
→ indexed/current hash check → bounded claim, decision, or audit finding
```

## 4. Core record contract

Core records use schema version `core/1.0`.

```json
{
  "schema_version": "core/1.0",
  "record_id": "claim_example",
  "record_kind": "claim",
  "study_id": "study_example",
  "occurred_at": "2026-08-04T10:00:00+09:00",
  "recorded_at": "2026-08-04T10:01:00+09:00",
  "status": "completed",
  "created_by": {"actor_id": "actor_researcher", "actor_type": "human"},
  "relations": [], "source_refs": [], "artifact_refs": [],
  "approval_refs": [], "payload": {}
}
```

### 4.1 Record kinds

- `research_plan`, `protocol`, `approval`, and `execution_session` describe
  authorized intent and execution.
- `observation`, `decision`, and `claim` distinguish measurement, choice, and
  supported/refuted assertion.
- `artifact`, `amendment`, `audit_finding`, and `contribution` retain lineage,
  correction, audit, and attribution.
- `negative_result` and `stopped_work` retain an unexpected result or a stop
  reason rather than hiding it.

### 4.2 Structural validation

Validators reject invalid record/study/actor/reference patterns, invalid ISO
8601 time values, unsupported fields, invalid actor types, malformed relations,
invalid artifact-revision hashes, empty evidence locators, and duplicate
approval/artifact/contribution references. JSON Schema and the dependency-free
validator have representative parity fixtures; date-time validation uses an
explicit format checker rather than environment-dependent annotations.

## 5. Expected, observed, interpretation, and uncertainty

| Layer | Meaning | Prohibited conflation |
|---|---|---|
| Expected | predeclared hypothesis, plan, or prediction | presenting it as a measurement |
| Observed | measured, executed, or verified fact | presenting an interpretation as fact |
| Interpretation | explanation or model of an observation | asserting causation or superiority |
| Uncertainty | limitation, missing condition, or unverified item | deleting it from the result |

A `claim` with `support_status: "supported"` requires human-verified evidence.
Unobserved expectations and semantic similarity cannot support that claim.

## 6. Evidence and artifact lineage

An evidence reference binds an artifact revision to a locator and verification
status.

```json
{
  "artifact_revision_id": "artifact_result@sha256:<sha256>",
  "locator": {"kind": "line_range", "path": "docs/result.md", "start": 10, "end": 24},
  "verification_status": "human_verified"
}
```

Relations such as `uses_protocol`, `generated_from`, `derived_from`,
`supported_by`, `refuted_by`, `validated_by`, `corrects`, and `supersedes`
express lineage. The core-to-index adapter preserves relation targets, source
paths, and artifact hashes, and retains the original core record as index raw
JSON so a projection cannot overwrite provenance.

## 7. Approval and execution governance

Only a separate, approval-checked helper may append to the canonical ledger.
Before an append, the record must name a real earlier `approval` record with
status `approved`, a human approver, explicit scope, and coverage of either the
record ID or its study and record kind. Draft/proposed records are not canonical
append candidates.

An active `execution_session` requires explicit approval. This separates an
agent's ability to search from authority to execute. The MCP has retrieval only;
it cannot authorize execution or append records.

## 8. Amendment, negative result, and stopped work

An original record is never overwritten. An amendment has exactly one
`corrects` relation, targets only allowed `/payload/...` fields, retains old
value/new value/reason together, and affects a resolved retrieval view only
when completed. Negative results and stopped work are canonical evidence, not
disposable error logs; they preserve selection pressure, condition changes,
stop reasons, and retry eligibility.

## 9. Agent operating constitution

`AGENTS.md` and `agents/AGENT_RULES.md` form an operating control plane.
Without explicit user authority an agent may not execute a shell command, write
or copy/delete files, install a package, test/build, use network/remote/
background work, run a benchmark, download a model, or start a daemon.

Before non-trivial work, the plan/work-log projection records requester,
proposer, decision-maker, planner, executor, verifier, exact commands and
paths, success criteria, exclusions, reference boundary, and secret boundary.
An unexpected result blocks later work until the cause is separated and a plan
inside the approved scope is renewed. `created_by` identifies human, AI, or
external-system work; a read-only audit finds AI-authored decisions lacking a
human review rather than attributing them automatically to the user.

## 10. Environment isolation and reference projects

```text
Reference project                         Universal Research project
─────────────────                         ──────────────────────────
Read-only schema/adapter input            Independent JSONL ledger
No historical result/session/DB writes    Independent derived indexes
No shared runtime database                Independent plan/work-log views
                                           Independent package/plugin/CI/release
```

The Project Profile defines the boundary. Reference artifacts and their
embedding databases may be inspected for design only; no source path, model
metric, hardware condition, or research result becomes a universal assumption.

## 11. Path and secret boundary

All runtime paths resolve under the configured project root. The source fetch
layer rejects absolute paths, parent traversal, symlink escape, sensitive file
patterns, and any path not registered by the canonical source/index registry.
Secrets, API keys, local environments, model binaries, and raw private prompts
are neither indexed nor copied into a ledger, benchmark fixture, report, or
release artifact by default.

## 12. Storage, search, and display adapters

### 12.1 Canonical storage

JSONL events are append-only. A record correction is a separate amendment; a
derived-index repair cannot alter canonical history.

### 12.2 Lexical adapter

The lexical SQLite/FTS view is built from validated canonical events and source
metadata. Refresh validates source existence and hashes, rejects duplicate event
IDs and broken references, writes a staged database, and promotes it only after
retrieval verification.

### 12.3 Optional semantic adapter

A semantic index is an optional derived view, never evidence authority. It may
be refreshed only from approved searchable fields after source validation. It
must retain model/version, dimension, event/passages count, artifact hashes, and
health status. A missing, partial, or stale semantic index cannot be reported
as ready search coverage.

### 12.4 Display adapter

Markdown work logs, TODO views, and external displays may be regenerated from
canonical records. They are useful interfaces, not a substitute for the ledger.

## 13. Read-only MCP contract

The MCP exposes candidate search, latest-record lookup, evidence fetch, ledger
audit, derived-index status, and governance-manifest preparation. It does not
write a ledger, approve a task, execute a model/benchmark, or start a remote
proxy. Search candidates are not evidence. An evidence response carries an
event ID, path, source line range, expected/indexed/current hashes, integrity
status, and bounded source content. `matched`, `mismatched`, and `not_indexed`
must be reported distinctly.

## 14. Codex plugin and Skill

The supported host integration is Codex. The plugin prepares validated task
packets, scope/governor receipts, evidence bundles, and dispatch manifests; it
does not create an agent session by itself. Codex retains ownership of agent
creation, model choice, tool permission, native parallelism, and display.

The fixed eleven-role governance roster is loaded from versioned prompt packs.
The `scope_and_cost_governor` assesses work before plan approval, while a
deterministic host controller—not an LLM reviewer—blocks calls outside a closed
approved boundary. Visualization remains disabled unless a task capability
scope and explicit user opt-in both permit it.

## 15. Validation and failure coverage

Fixtures and CI cover fabricated/AI/out-of-scope approvals, malformed records,
missing human review, source relations and raw provenance, indexed/current hash
match and post-index mutation mismatch, sensitive paths and symlink escape,
canonical-input byte preservation, package entry points, plugin path
independence, scope/budget gates, and failure-record behavior. Heavy semantic
encoder compatibility checks are separate from the default CI.

## 16. Package, CI, and release

The package name is `universal-research-mcp`. Its default dependency set is a
read-only MCP runtime; optional semantic dependencies are intentionally not
downloaded by default. CI runs package install, console entry point, core,
approval, MCP, and lexical-fixture checks on pushes and pull requests. It does
not run a semantic encoder, model download, benchmark, reference project, or
external provider.

PyPI publication is a separate GitHub Release-published workflow: build source
distribution/wheel, run `twine check`, publish through GitHub OIDC Trusted
Publishing, and create a digital attestation. It needs only `contents: read`
and `id-token: write`; no PyPI token is stored in source, logs, or repository
secrets.

## 17. Operating summary

> AI may find, structure, and audit research records. It may not alter a record
> because of a search result, execute beyond an approval boundary, or intrude
> on a reference research environment.

Universal Research is therefore more than an embedding database or memory MCP:
it is a research operations control plane for provenance, approval, environment
isolation, reproducibility, and negative-result preservation.
