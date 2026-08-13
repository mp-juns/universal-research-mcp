# Universal Research MCP

Universal Research MCP is a provenance-first, append-only research operations
framework and read-only MCP. It records plans, approvals, observations, claims,
failures, amendments, and contributions with traceable sources. Canonical JSONL
is authoritative; SQLite search indexes are verified, replaceable derived views.

> **Supported integration (0.4.0): Codex only.** Codex owns model
> selection, agent sessions, tool execution, approvals, and GUI presentation.
> Ollama, OpenAI API, Anthropic API, Moonshot/Kimi, Claude Code, OpenCode, and
> OpenClaw are not supported or invoked by this release.

The repository can contain provider or runtime prototypes for future research.
They are not exposed through the default CLI, Codex plugin, MCP tools, or
package entry points, and are not a compatibility promise.

## Quick start

### Secure Codex/Docker harness preview

The opt-in secure harness keeps Codex on the host and runs only sealed test,
build, and experiment recipes in offline, resource-bounded Docker workers. The
original project is never mounted into a worker, visualization is disabled, and
edits stay quarantined until their exact diff hash is approved. See
[the secure harness guide](docs/secure-harness.md). This preview does not claim
to be a custom Codex runtime or a complete multi-provider agent scheduler.


Python 3.11 or newer is required.

```bash
python -m pip install universal-research-mcp
universal-research init ./my-research
universal-research serve --root ./my-research
```

When run directly in a terminal, `serve` shows its startup phases on the
terminal's status stream—for example index check, staged rebuild, verification,
and `100% ready for MCP requests`. This never writes to the MCP protocol
stream. It is enabled by default for an interactive terminal and disabled by
default for a non-interactive MCP host. Override it with
`--startup-progress` or `--no-startup-progress`.

`init` creates an independent empty canonical source registry and a verified
FTS5 lexical index. It reports the semantic index as `missing`; it does not
silently create an empty semantic database, download a model, or contact an
embedding API. A lexical refresh that does not alter canonical JSONL is staged,
validated, and atomically replaced. Select the research root with `--root` or
`UNIVERSAL_RESEARCH_ROOT`.

### Optional semantic and hybrid candidate retrieval

After canonical records exist, semantic retrieval is an explicit project-level
choice. The offline demo backend creates no download, GPU, credential, or
network request, and is useful for checking the full semantic/hybrid workflow:

```bash
universal-research semantic configure --backend demo --root ./my-research
universal-research semantic build --root ./my-research
universal-research semantic status --root ./my-research
```

The demo identifies itself as `backend_class=deterministic_demo` and
`trained_embedding_model=false`. It is a reproducible retrieval fixture, **not**
a learned embedding model or a quality claim. For a pinned model already present
on disk, configure `--backend local --model-path /absolute/path/to/model`; the
command never downloads a snapshot. A configured semantic view is rebuilt only
when `--auto-refresh` was explicitly selected; otherwise canonical writes leave
it stale and print a recovery command. Remote embedding remains unsupported by
the public MCP and is never invoked by `serve`.

See the full [canonical input tutorial](docs/input-cli-tutorial.md) for a
source → human approval → record append → lexical search workflow.

### First searchable input

The MCP remains read-only. A host-owned CLI is the only canonical write path:

```bash
universal-research init ./my-research
# Create ./my-research/docs/note.md yourself, then register that immutable path.
universal-research source register docs/note.md --root ./my-research \
  --source-id src_note_v1 --source-type markdown
universal-research record template > protocol.json
universal-research record validate protocol.json --root ./my-research
```

For a governed record, first append a human `record_kind=approval` record with
`record approve --confirm <record-id>`, then append exactly one non-approval
record with its matching `--approval-ref`. `recorded_at` determines the
append-only `data/events/daily/YYYY-MM-DD/events.jsonl` destination. Each
successful append refreshes lexical search; a refresh failure leaves the
canonical append intact, reports stale state, and prints the recovery command.
Registered paths are immutable revisions: editing or re-registering one is
rejected; register a new project-contained path instead.

The default `universal_research` MCP provides:

- candidate search and event/hash-bound source re-verification;
- an eleven-role governance contract and Codex dispatch-manifest preparation;
- scope/cost preflight, deterministic operation-gate, and failure-tombstone
  preparation; and
- lexical, semantic, and RRF hybrid candidate retrieval plus derived-index
  status.

It never approves work for a user, writes a canonical ledger, invokes a remote
API, downloads a model, or starts a benchmark or daemon. Query-time semantic
search uses only the explicitly configured offline backend; an unconfigured or
stale semantic view fails closed. Provider fallback is future work.

## Evidence flow

```text
canonical JSONL → staged/verified SQLite candidate → memory_fetch_evidence
with event_id + expected_sha256 → current-hash check → bounded claim
```

`memory_search_candidates` accepts `mode="lexical"`, `"semantic"`, or
`"hybrid"`. Hybrid search independently ranks lexical and semantic candidates
and fuses ranks with reciprocal-rank fusion (0.45 lexical / 0.55 semantic,
`k=60`). Every result remains `candidate_only=true`; semantic similarity cannot
establish a fact, cause, comparison, or release claim. A semantic passage may
choose the returned path and exact line range, but it still must go through
`memory_fetch_evidence` and `memory_gate_claim`.

Search results are candidates, not evidence. For a consequential conclusion,
retain the evidence fetch's event ID, path, line range, expected and current
hashes, and `integrity_status`. Files not registered in the index cannot be
fetched. Search, latest, and evidence fetch fail closed while the lexical index
is stale and require `universal-research index ensure --kind lexical`. When a
file's current hash differs, content is withheld by default; only the explicit
diagnostic `allow_mismatched_content=true` response includes current content.

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
visualization, that category is `unavailable`, not zero or an estimate.

## Benchmark status

The repository provides a paired A/B protocol, fixture contracts, and scoring
contracts. No confirmatory live A/B result is currently available. Future runs
must use the same model, prompt, tasks, permissions, and source snapshot within
each pair; retain failures; use paired repetitions; and preserve raw host
telemetry. The only reportable public metrics are citation correctness,
unsupported-claim rate, evidence retrieval success, total tokens, tool calls,
latency, cost, and paired differences. Missing telemetry is `unavailable`,
never inferred. See [the benchmark disclosure](docs/benchmark-disclosure.md).

## Boundaries and data authority

Reference projects are read-only design inputs. Universal Research does not
create, modify, delete, move, or append their files, event logs, databases,
results, or session records. Their embedding databases may be read only to
understand a schema, metadata, or adapter design. The new project never copies
or shares a reference runtime database.

Semantic/hybrid retrieval is a hardened successor to the public
[Evidence-First Research Memory predecessor prototype](https://github.com/mp-juns/evidence-first-research-memory): it retains separate lexical/semantic
ranking and source-range candidates, while adding registered-file SHA-256
re-verification, stale fail-closed behavior, and deterministic claim gating.

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
  core/            canonical-input and record contracts
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

The 0.4.0 namespace is intentionally breaking: public Python modules live under
`universal_research_mcp.*`; legacy general top-level package imports are not
provided as shims. Independently reviewed adapters for other hosts remain
separate work.
