# Universal Research Framework Architecture

> Public integration scope for 0.3.1 is Codex only. Provider-backed runtime
> modules below are retained as internal prototypes and are not registered by
> the plugin, exposed as distribution entry points, or covered by a public
> compatibility promise.

## Authority flow

```text
append-only canonical JSONL
  → lexical / semantic derived indexes
  → candidate search
  → source or artifact verification
  → bounded claim / decision / audit finding
```

The Markdown TODO, WORK_LOG, and session notes are display adapters. They are
not canonical authority. A source-grounded conclusion requires an artifact
revision and a line, page, row, or structured-data locator.

## Implemented boundaries

- `schemas/` defines the versioned core, pack, and project-profile contracts.
- `core/ledger.py` validates core records and legacy event compatibility without
  writing data.
- `core/amendments.py` creates a narrow, fail-closed resolved view for completed
  payload amendments without changing canonical records.
- `core/proposals.py` supplies an explicit approval-checked append boundary;
  it resolves the referenced approval record and requires approved, human,
  explicitly scoped authority. The MCP intentionally does not call it.
- `core/audit.py` derives read-only findings with record-addressable evidence.
- `scripts/validate_research_ledger.py` is a read-only ledger validation entry
  point.
- `core/indexing.py` projects Core 1.0 records into a compatibility retrieval
  document; canonical JSONL remains unchanged and is retained as `raw_json`.
- `mcp/research_memory/` is the source-tree compatibility launcher for the
  unified installable MCP. Evidence fetch is allowlisted to indexed source
  candidates and verifies the exact event/hash revision.
- `universal_research_mcp/indexing/` initializes empty databases and promotes
  lexical/semantic rebuilds only after staging, provenance, vector, retrieval,
  and integrity checks. Failed builds preserve the previous good database and
  emit derived health state.
- `universal_research_mcp/providers/` is an internal prototype that separates embedding from generation,
  keeps credentials behind env/keyring references, routes local-first, and
  requires explicit remote budgets without automatic retry.
- `universal_research_mcp/harness/` is an internal prototype for validated independent packets in
  bounded parallel batches through an injected host/provider executor. It runs
  the scope/cost governor first, records a receipt bound to exact worker/scope
  hashes, requires explicit per-agent costs, and never force-kills an accepted
  remote call.
- `universal_research_mcp/agent_runtime/` is an internal prototype for project-local, hash-bound
  plugin-owned sessions after governor review. These are separate provider
  requests and records, not native host GUI tasks or operating-system sandboxes.
- `universal_research_mcp/` publicly provides the default read-only memory MCP
  and one management CLI. They accept an explicit project root and never use a
  reference-project runtime path. Provider execution modules are not registered
  by the Codex plugin or exposed through a distribution console entry point.
- The MCP transport requires the documented `mcp[cli]` runtime. Pure ledger and
  lexical-query contract tests do not import that optional transport runtime.
- `packs/` adds constraints without relaxing the core policy.

## Deliberately outside the MCP authority

- Canonical ledger writes or automatic amendments
- Unapproved index/model/provider execution or background watchers
- Automatic audit dispositions or approval decisions
- Reference-project data migration or runtime sharing

## Governed multi-agent foundation

`core/governance.py` adds an execution-backend-independent control plane for a
fixed eleven-role research roster. It validates task packets and decision records,
enforces Lightweight/Benchmark/Final-review activation, blocks reviewer
execution authority, and derives user-decision claim gates from critical/high
findings. Provider-neutral execution is isolated in the bounded harness rather
than granted to the governance role itself.

`core/index_refresh.py` separates a canonical research event from its derived
search projection: it decides whether a recorded event is eligible to trigger a
refresh and validates the resulting index-health record, but it does not write
an index. `docs/multi-agent-governance.md` specifies the full policy.
