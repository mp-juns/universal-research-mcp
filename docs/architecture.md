# Universal Research Framework Architecture

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
  the MCP intentionally does not call it.
- `core/audit.py` derives read-only findings with record-addressable evidence.
- `scripts/validate_research_ledger.py` is a read-only ledger validation entry
  point.
- `mcp/research_memory/` is a local, read-only lexical retrieval adapter. It
  does not start a daemon, load a model, or expose writes.
- The MCP transport requires the documented `mcp[cli]` runtime. Pure ledger and
  lexical-query contract tests do not import that optional transport runtime.
- `packs/` adds constraints without relaxing the core policy.

## Deliberately not implemented

- Canonical ledger writes or automatic amendments
- Index building, semantic model loading, or background watchers
- Automatic audit dispositions or approval decisions
- Reference-project data migration or runtime sharing
