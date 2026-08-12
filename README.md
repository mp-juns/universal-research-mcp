# Universal Research MCP

Universal Research MCP is a provenance-first, append-only research operations
framework and read-only MCP. It records plans, approvals, observations, claims,
failures, amendments, and contributions with traceable sources. Canonical JSONL
is authoritative; SQLite search indexes are verified, replaceable derived views.

> **Supported integration (0.4.1): Codex only.** Codex owns model
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

The default `universal_research` MCP provides only:

- candidate search and event/hash-bound source re-verification;
- deterministic claim eligibility receipts for material results, comparisons,
  causal statements, release decisions, and explicitly material facts;
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
with event_id + expected_sha256 → current-hash check → memory_gate_claim →
eligible or blocked material claim
```

Search results are candidates, not evidence. For a consequential conclusion,
retain the evidence fetch's event ID, path, line range, expected and current
hashes, and `integrity_status`. Files not registered in the index cannot be
fetched. Search, latest, and evidence fetch fail closed while the lexical index
is stale and require `universal-research index ensure --kind lexical`. When a
file's current hash differs, content is withheld by default; only the explicit
diagnostic `allow_mismatched_content=true` response includes current content.

`memory_gate_claim` is a deterministic, fail-closed receipt for material
claims. It re-fetches each supplied exact evidence reference and accepts only a
current registered revision. Release, comparison, and causal claims require two
distinct verified records; a material factual or result claim requires one.
Routine lookups are not gated. The gate checks evidence eligibility, not the
scientific truth of prose, and it does not invoke a model or write the ledger.

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

The repository provides paired A/B protocols, fixture contracts, and scoring
contracts. No confirmatory live A/B result is available.

![Exploratory measured claim-safety diagnostics: ordinary task token and latency overhead, plus post-index mutation outcomes](docs/assets/exploratory-claim-safety-diagnostic-v1.png)

The graphic reports two public, exploratory live diagnostics using synthetic
sources, `gpt-5.6-terra`, low reasoning effort, and one run per condition. On
four ordinary single-source tasks, both conditions achieved 4/4 factual answers
with evidence-line citations; the MCP condition used 2.26× input tokens and
2.02× wall latency. In the separate six-trial post-index source-mutation
diagnostic, direct filesystem retrieval accepted the changed source as verified
evidence in 6/6 trials, while MCP-gated retrieval accepted it in 0/6 and
abstained correctly in 6/6. This is bounded evidence for stale-source
protection, not evidence of general hallucination reduction, research-quality
improvement, or model superiority. See the machine-readable
[directional source diagnostic](benchmarks/results/codex-directional-v1.json)
and the [claim-safety pilot](benchmarks/results/codex-claim-safety-v3.md).

The narrower public development protocol
[`integrity-claim-gate-v1`](benchmarks/protocols/integrity-claim-gate-v1.md)
has now been executed once: 24 public synthetic tasks × 4 conditions = 96
participant runs, with a separate condition-blinded evaluator. The observed
MCP + Claim Gate condition had 2/18 unsafe material assertions on fault tasks
(11.1%), versus 4/18 (22.2%) for direct filesystem; clean supported-claim
coverage was 100.0% versus 66.7%. It cost 1.55× mean execution tokens and
1.61× mean latency. The paired 95% interval for the unsafe-assertion
difference includes zero, so this is **not** proof of an effect. It is a
development-sample signal plus a measured burden and known semantic failure
modes. See the complete
[development result](benchmarks/results/integrity-claim-gate-v1-development-20260813.md).

![Integrity & Claim-Gating v1 development results: safety, clean coverage, and execution burden](docs/assets/integrity-claim-gate-v1-development-20260813.png)

Future runs must use the same model, prompt, tasks, permissions, and source
snapshot within each pair; retain failures; use paired repetitions; and
preserve raw host telemetry. The only reportable public metrics are citation
correctness, unsupported-claim rate, evidence retrieval success, total tokens,
tool calls, latency, cost, and paired differences. Missing telemetry is
`unavailable`, never inferred. See [the benchmark disclosure](docs/benchmark-disclosure.md).

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
