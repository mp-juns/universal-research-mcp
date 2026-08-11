# TODO

## 2026-08-12 — Universal Research MCP 0.4.0 implementation

- Requester: user
- Proposer: previous planning agent
- Decider: user (plan supplied as implementation intent)
- Planner: previous planning agent
- Executor: Codex
- Verifier: Codex

### Approved scope

Implement the supplied 0.4.0 breaking-release plan in this repository only:

1. Add a host-owned canonical-input CLI (`source register`, `record template`,
   `record validate`, `record append`, and `record approve`) with approval and
   source-integrity gates, daily JSONL routing, and lexical-index refresh.
2. Make stale lexical indexes and content-hash mismatches fail closed by
   default, including the MCP evidence tool and aliases.
3. Remove unconfirmed live A/B benchmark results from public documentation and
   retain only the protocol/disclosure contract.
4. Move distributable code from the general top-level packages into the
   `universal_research_mcp` namespace without compatibility shims; update
   package metadata, entry points, tests, CI, and wheel validation.
5. Add repository configuration files for the requested GitHub release and
   protection policy where that can be represented as versioned configuration.

### Commands and files

Allowed local commands: repository inspection, `python -m pytest`, `ruff`,
`mypy`, `python -m build`, and temporary local virtual-environment smoke tests.
Expected edited areas: `universal_research_mcp/`, migrated package modules,
`tests/`, `docs/`, `README.md`, `benchmarks/`, `pyproject.toml`,
`.github/`, `scripts/`, `TODO.md`, and `WORK_LOG.md`.

### Success criteria

The source-to-approval-to-record-to-lexical-search-to-hash-verified-evidence
path succeeds; invalid approval/scope/ID/source/staleness cases fail closed;
the default evidence response withholds mismatched contents; the package
contains only the new namespace; and applicable local tests/build smoke pass.

### Exclusions

No network access, package installation, remote GitHub/PyPI mutation, live
model/API call, benchmark execution or publication of fresh metrics,
visualization, or writes to any reference project, external DB, or index.

### 2026-08-12 follow-up authorization

The user explicitly requested completion of the remaining release operations.
Authorized remote scope is limited to this repository: create an implementation
branch and draft PR; set the requested description/topics and `main` branch
protection; merge the reviewed/green PR; create/push `v0.4.0`; and allow the
tag-only workflow to publish to PyPI and create its GitHub Release. No other
repository, reference project, external data store, benchmark, model/API run,
or package installation is in scope.
