# Universal Research MCP

[![PyPI](https://img.shields.io/pypi/v/universal-research-mcp.svg)](https://pypi.org/project/universal-research-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/universal-research-mcp.svg)](https://pypi.org/project/universal-research-mcp/)
[![CI](https://github.com/mp-juns/universal-research-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/mp-juns/universal-research-mcp/actions/workflows/ci.yml)

Universal Research MCP turns research retrieval into a verifiable evidence
workflow. Search results remain candidates until the server re-reads the exact
registered source range and confirms its current SHA-256 revision.

```text
candidate retrieval
  -> exact source fetch
  -> revision integrity check
  -> evidence eligibility check
  -> relevance/conflict review by the host
  -> supported claim or explicit abstention
```

It is a **read-mostly MCP**. Canonical writes exist only behind a two-step,
hash-bound ingest transaction with an external one-time human approval receipt.

## Five-minute local demo

```bash
python -m pip install universal-research-mcp
universal-research init ./my-research
universal-research serve --root ./my-research
```

Register the server in Codex:

```toml
[mcp_servers.universal_research]
command = "universal-research"
args = ["serve", "--auto-index"]
cwd = "/absolute/path/to/my-research"
```

The empty project is intentional: the MCP does not crawl arbitrary files.
Follow the [input tutorial](docs/input-cli-tutorial.md) to register immutable
sources, create an approved record, and make it searchable.

## Why this is not ordinary RAG

| Ordinary retrieval | Universal Research |
|---|---|
| Search result may be quoted directly | Search result is `candidate_only` |
| Index content may silently become stale | Current source hash is compared with the registered revision |
| Similarity is treated as support | Similarity cannot establish truth or causality |
| Corrections may overwrite history | Canonical JSONL is append-only |
| Writes depend on agent intent | Ingest requires immutable draft + external receipt + recoverable transaction |

The evidence eligibility check proves only that submitted evidence is current,
registered, range-valid, and sufficient in count for the declared claim type.
It does **not** prove that the evidence supports the claim, reconcile conflicts,
or establish that a source is true. Those are separate host review stages.

## Supported surface

- lexical, local semantic, hybrid, and adaptive candidate retrieval
- exact source-range fetch with fail-closed revision checks
- deterministic evidence eligibility receipts
- append-only canonical records and recoverable, journaled ingest
- fixed-role Codex governance contracts
- a default-deny agent-creation disclosure and one-time approval binding for
  governed provider/secure-harness execution
- a Docker secure harness for sealed benchmark/final-review execution
- a reviewed, unauthenticated public-demo transport for static corpora

The PyPI wheel intentionally excludes the repository's experimental OpenAI,
Anthropic, agent-runtime, and provider-harness packages. Codex remains the only
supported host integration. Optional local SentenceTransformer embeddings use
an already-present pinned snapshot and never imply generation-provider support.

See [semantic retrieval](docs/semantic-retrieval.md),
[secure harness](docs/secure-harness.md), and
[host integration](docs/host-integration.md).

## Public read-only demo

Publishing a corpus is a separate explicit action. The manifest binds every
canonical JSONL file, registered source, and derived index used by the server.

```bash
universal-research public-demo prepare \
  --root ./my-research \
  --corpus-id reviewed-demo \
  --display-name "Reviewed Demo" \
  --confirm-public-data I_UNDERSTAND_THIS_DATA_WILL_BE_PUBLIC

universal-research public-demo verify --root ./my-research

universal-research serve \
  --root ./my-research \
  --transport streamable-http \
  --public-demo \
  --host 127.0.0.1 \
  --port 8765
```

The bundled server is not a multi-tenant service. Internet deployment still
needs TLS termination, authentication where applicable, rate limits, tenant
isolation, monitoring, and a separately reviewed deployment boundary. See the
[public demo guide](docs/public-demo.md) and [security model](docs/security.md).

## Measured development evidence

The public synthetic development run contains 24 tasks × 4 conditions. The
MCP + historical “Claim Gate” condition made 2/18 unsafe material assertions
on fault tasks versus 4/18 for direct filesystem retrieval, while using 1.55×
mean execution tokens and 1.61× mean latency. The paired 95% interval includes
zero. This is a development signal and measured cost, **not proof of general
hallucination reduction or research-quality improvement**.

![Development evidence-eligibility results](docs/assets/integrity-claim-gate-v1-development-20260813.png)

See the [complete development result](benchmarks/results/integrity-claim-gate-v1-development-20260813.md)
and [benchmark disclosure](docs/benchmark-disclosure.md). Historical artifact
names retain “claim gate” for provenance; the current product contract is
“evidence eligibility.”

The [completed-results index](benchmarks/results/README.md) also collects the
earlier directional and safety pilots, including their negative findings and
measured overhead. The latest completed
[A/B/C integration diagnostic (2026-08-26)](benchmarks/results/abc-integration-diagnostic-20260826.md)
retains one public synthetic task per condition, all seven preceding startup
diagnostics, an input-budget stop, and unknown usage. Its strict automatic
scores were 0/1, 0/1, and 1/1, with **unequal input budgets and no repetitions**.
This is source-checkout integration evidence, not a released-package efficacy
measurement or proof that one condition is better. No in-progress experiment
results are included in these completed-result summaries.

## Authority model

1. `data/events/` is the canonical append-only ledger.
2. `data/index/` contains rebuildable derived views.
3. Registered original sources are stronger than either index.
4. Candidate retrieval never grants claim eligibility.
5. Evidence eligibility never proves semantic support or truth.
6. Host approval remains separate from MCP validation.
7. Governed agent creation requires the user-visible reason, tasks, count,
   direct alternative, token/time ranges, and scope to be hash-bound before a
   one-time approval is consumed.

Canonical ingest uses a write-ahead transaction journal. Each target file is
bound to exact before/after hashes; a failure after a partial append leaves the
draft in `recovery_required` and the same one-time receipt can resume only that
exact transaction. The draft is marked consumed after all canonical operations
are verified.

Architecture decisions:

- [ADR-0001: canonical authority and derived views](docs/decisions/0001-canonical-authority.md)
- [ADR-0002: recoverable multi-file ingest](docs/decisions/0002-recoverable-ingest.md)
- [ADR-0003: supported wheel versus experimental source](docs/decisions/0003-supported-wheel-surface.md)

## Non-goals

Universal Research is not:

- a truth oracle or automated scientific peer reviewer
- an authenticated private remote MCP or multi-tenant SaaS
- a replacement for Codex, Claude Code, or another agent host
- a hidden provider router or credential store
- evidence that a model, method, or research result is correct

For medical, legal, regulated, or safety-critical decisions, use it only as an
audit and evidence-handling aid with qualified human review.

## Development

```bash
python -m pip install ".[test]"
python -m pytest -q
ruff check universal_research_mcp
mypy --no-incremental --cache-dir=/dev/null universal_research_mcp
python -m build
python scripts/validate_distribution_artifact.py dist/*.whl
python scripts/ci_smoke.py dist/*.whl
```

Release workflows pin third-party actions to exact commits. A release wheel is
built once, validated on Linux/macOS/Windows, and the same artifact is published
through PyPI Trusted Publishing only after every release gate succeeds.

License: [MIT](LICENSE)
