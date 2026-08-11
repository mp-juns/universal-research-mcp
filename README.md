# Universal Research MCP

Universal Research MCP is a provenance-first, append-only research operations
framework and read-only MCP. It records plans, approvals, observations, claims,
failures, amendments, and contributions with traceable sources. Canonical JSONL
is authoritative; SQLite search indexes are verified, replaceable derived views.

> **Supported integration (0.3.1 preview): Codex only.** Codex owns model
> selection, agent sessions, tool execution, approvals, and GUI presentation.
> Ollama, OpenAI API, Anthropic API, Moonshot/Kimi, Claude Code, OpenCode, and
> OpenClaw are not supported or invoked by this release.

The repository can contain provider or runtime prototypes for future research.
They are not exposed through the default CLI, Codex plugin, MCP tools, or
package entry points, and are not a compatibility promise.

## Quick start

Python 3.11 or newer is required.

```bash
python -m pip install universal-research-mcp
universal-research init ./my-research
universal-research serve --root ./my-research
```

`init` creates an independent empty canonical source registry and a verified
FTS5 lexical index. It reports the semantic index as `missing`; it does not
silently create an empty semantic database or download a model. A lexical
refresh that does not alter canonical JSONL is staged, validated, and atomically
replaced. Select the research root with `--root` or
`UNIVERSAL_RESEARCH_ROOT`.

The default `universal_research` MCP provides only:

- candidate search and event/hash-bound source re-verification;
- an eleven-role governance contract and Codex dispatch-manifest preparation;
- scope/cost preflight, deterministic operation-gate, and failure-tombstone
  preparation; and
- lexical and semantic derived-index status.

It never approves work for a user, writes a canonical ledger, or invokes a
model, API, benchmark, daemon, or remote provider. Query-time search is lexical
only in this release. Dense embeddings and provider fallback are future work.

## Evidence flow

```text
canonical JSONL → staged/verified SQLite candidate → memory_fetch_evidence
with event_id + expected_sha256 → current-hash check → bounded claim
```

Search results are candidates, not evidence. For a consequential conclusion,
retain the evidence fetch's event ID, path, line range, expected and current
hashes, and `integrity_status`. Files not registered in the index cannot be
fetched.

## Governed Codex agents

`scope_and_cost_governor` runs before plan approval in every mode. It assesses
necessity, a bounded time estimate, work units, difficulty, compute/network
cost, and evidence; it does not approve execution or kill a process. The common
operation gate performs a declarative preflight bound to an approved
`scope_hash` and always returns `execution_authorized=false`. At the real tool
boundary, the Codex host must compare the gate hash with a closed,
action-specific argument envelope before deciding whether to execute.

The plugin prepares a role-specific task packet, hash-bound scope receipt, and
Codex dispatch manifest over one evidence snapshot. A manifest does not start
an agent. Codex may create native subagents and decide their sessions, model,
parallelism, and GUI surface under host permissions and the user's entitlement.
The plugin does not bypass a Codex subscription by routing work to a paid API.

The default failure policy is `blocking_only + ask + redacted`. A minimum
failure tombstone is always retained, while detail retention is configurable as
`full | metadata_only | ask` and `full | redacted | hashes_only`. There is no
unrecorded `off` mode.

Host visualization is off by default. It requires both explicit user opt-in and
a separately approved capability scope/plan reference. Permission to generate a
normal data plot does not imply permission to invoke a host visualization skill.

Token accounting uses only exact counts supplied by a provider or host. If the
host does not expose an exact count for commands, code generation, Skills, or
visualization, that category is `unavailable`, not zero or an estimate. See
[the benchmark disclosure](docs/benchmark-disclosure.md) for the one archived
development measurement published with this preview.

## Development benchmark disclosure

An archived paired Codex-host measurement compared direct file lookup with
evidence that the real read-only MCP had already searched and hash-verified.
It used **10 public synthetic lexical questions** (20 paired calls),
`gpt-5.6-terra` at high reasoning effort, and no external provider API.

| Observed development measurement | Direct file | Verified MCP prefetch |
| --- | ---: | ---: |
| Source-text fact matches on the later non-mutating audit | 10 / 10 | 10 / 10 |
| Host-reported non-overlapping tokens | 229,372 | 113,931 |
| Shell command events after treatment prefetch | 20 | 0 |

Under that exact fixture and accounting definition, the prefetch treatment
reported 115,441 fewer non-overlapping tokens (50.3%). This is **not** a price,
latency, general-capability, or research-quality claim. The original strict
scorer recorded the run as `terminal_failed` because its answer-format contract
rejected otherwise source-matching outputs; the table uses a separately
recorded, non-mutating source-text audit. The fixture was deliberately simple,
and the treatment received centrally pre-fetched evidence, so it is not an
end-to-end agent-runtime comparison. Full limitations and result status are in
[the benchmark disclosure](docs/benchmark-disclosure.md).

## Boundaries and data authority

Reference projects are read-only design inputs. Universal Research does not
create, modify, delete, move, or append their files, event logs, databases,
results, or session records. Their embedding databases may be read only to
understand a schema, metadata, or adapter design. The new project never copies
or shares a reference runtime database.

1. `data/events/` JSONL is the canonical event ledger.
2. `data/index/` SQLite and any embedding index are rebuildable derived views.
3. Original sources and artifacts verify candidate results.
4. Embedding similarity alone cannot establish a fact, cause, or performance
   claim.

Project-local working notes, when used, are not canonical records and are not
distributed with the package.

## Architecture

```text
universal_research_mcp/
  plugin/          Codex plugin and Skills
  governance/      fixed-role governance contracts and validators
  integrations/    host-specific dispatch adapters
  data/events/     independent canonical append-only events
  data/index/      independent derived lexical/dense indexes
  docs/            specifications and usage documentation
```

The core schema defines immutable records and typed relations. Study-type and
domain packs may add restrictions but may not relax core policy. A project
profile selects paths, adapters, and reference boundaries. The read-only MCP
returns candidate metadata and exact source evidence; it is not an execution or
ledger-write interface.

## Current support and next steps

This release supports the Codex plugin, local lexical lifecycle,
source-grounded evidence fetch, eleven-role governance, and non-executing Codex
dispatch preparation. Installation, MCP startup, and CI do not invoke an API,
local model, model download, benchmark, or background watcher.

Next work is deliberately separate: a namespace migration for compatibility
packages, stronger Codex host-dispatch installation fixtures, and independently
reviewed adapters for local/OpenAI/Anthropic/Moonshot providers or other hosts.
