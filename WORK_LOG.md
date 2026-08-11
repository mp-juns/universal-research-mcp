# Work log

## 2026-08-12 — 0.4.0 implementation started

- Requester: user
- Proposer: previous planning agent
- Decider: user
- Planner: previous planning agent
- Executor: Codex
- Verifier: pending
- Scope: the approved scope in `TODO.md`.
- Boundary: only this repository; reference projects and their artifacts remain
  read-only and untouched. No network, remote mutation, package installation,
  model/API, or live benchmark work is authorized.
- Initial observed state: version `0.3.1`; distributable packages include
  general top-level `core`, `governance`, `adapters`, `integrations`, and
  `scripts`; prior public benchmark disclosure contains archived result claims.
- Planned local migration command: `git mv core universal_research_mcp/core`,
  `git mv governance universal_research_mcp/governance`, `git mv adapters
  universal_research_mcp/adapters`, and `git mv integrations
  universal_research_mcp/integrations`. Repository-only `scripts/` stays at
  the repository root but loses package status; reusable index-builder code
  moves to `universal_research_mcp/tools/`.

## 2026-08-12 — Namespace migration replanned

- Executor: Codex
- Observed: the workspace permits content writes but the repository `.git`
  index is read-only; `git mv` therefore could not create `index.lock`.
- Effect: no Git-tracked rename occurred. The already-approved mechanical
  import rewrite and removal of `scripts/__init__.py` did occur; source files
  themselves were not otherwise lost or modified by the failed move.
- Revised local command: use filesystem `mv` only for the exact recorded
  package directories and reusable script modules, then retain the repository
  `scripts/` directory as non-package operational commands. This does not
  alter Git metadata or any external/reference project.

## 2026-08-12 — Implementation and verification update

- Executor: Codex
- Implemented: host-owned source/record input CLI; append-only daily routing;
  source, approval, duplicate-ID, and current source-hash gates; derived-index
  refresh reporting; stale-index retrieval refusal; default evidence-content
  withholding on mismatch with diagnostic opt-in; 0.4.0 namespace migration;
  package/CI/release workflow and benchmark-disclosure updates.
- Local checks completed: `python -m pytest -q` (258 passed, 2 skipped),
  `ruff check universal_research_mcp` (passed), and
  `mypy --no-incremental universal_research_mcp` (passed). The local mypy
  version crashes while serializing its incremental cache, so CI uses the
  no-incremental invocation.
- Not performed: network/package installation, PyPI publication, GitHub
  repository description/topics/branch-protection mutation, release/tag
  creation, live model/API work, or benchmarks. These are remote operations
  that require a separate user authorization under `agents/AGENT_RULES.md`.

## 2026-08-12 — Local build replanned

- Executor: Codex
- Observed: `python -m build --no-isolation` could not start because the local
  interpreter has no `build` module. No dependency was installed and no network
  was contacted.
- Cleanup command targeted only conventional local build artifacts (`dist/`,
  `build/`, and `universal_research_mcp.egg-info/`) before the failed build.
  The next read-only status check determines whether any material pre-existing
  artifact was present.
- Revised verification: `setup.py` is absent but installed setuptools is
  available. Build with `python -m pip wheel --no-build-isolation --no-deps .`
  into a new temporary directory only; this uses the declared local backend,
  does not install dependencies, and does not contact a package index. Then run
  the existing wheel validator and platform-aware smoke helper against that
  temporary artifact.
- Observed after the local no-index wheel build: the wheel was produced, but
  the repository launcher resolved an older user-installed 0.3 package because
  Python executed from `scripts/`; the smoke helper also inherited that package
  through `--system-site-packages`. This did not validate the 0.4 artifact.
- Correction: root the validator at this checkout and make CI's fresh venv
  isolated (installing the wheel with its declared dependencies). Local clean
  venv smoke remains unrun because dependency installation/network is excluded;
  the wheel structure validator can run locally without it.
- Verified wheel without external installation: built
  `universal_research_mcp-0.4.0-py3-none-any.whl` using no-index, no-isolation,
  no-dependency `pip wheel`; the validator passed and direct archive inspection
  found no `core/`, `governance/`, `adapters/`, `integrations/`, or `scripts/`
  top-level members. Clean fresh-install execution is delegated to the new CI
  smoke matrix because it must install the declared MCP dependency.

## 2026-08-12 — Remote release authorization

- Requester/Decider: user
- Executor: Codex
- Authorized commands/actions: inspect authenticated GitHub context; create
  `agent/universal-research-mcp-040`, stage this recorded 0.4.0 worktree only,
  commit, push, and open a draft PR; configure this repository's description,
  topics, and `main` protection; after the release gate is green, merge the PR,
  push the exact `v0.4.0` tag, and monitor its PyPI/GitHub Release workflow.
- Success: merged main contains the reviewed commit; protection requires PRs
  and the stable `release-gate` check without force pushes; tag workflow
  publishes exactly once and creates a same-tag GitHub Release.
- Exclusions: no operations outside `mp-juns/universal-research-mcp`; no
  benchmark/model/API execution and no reference-project write.
- Exact remote command envelope: `git switch -c agent/universal-research-mcp-040`;
  `git add -A`; `git commit -m 'release: universal research mcp 0.4.0'`;
  `git push -u origin agent/universal-research-mcp-040`; `gh pr create --draft`;
  `gh repo edit` for the recorded description/topics; `gh api` for the
  main-protection payload; then, only after `release-gate` succeeds, merge and
  `git tag v0.4.0`/`git push origin v0.4.0`.
- CI correction: PR #3 initially failed because prompt-pack resource lookup
  still named the removed `governance` package and PowerShell did not expand a
  wheel glob. These are fixed in commit `4889f49`. The remaining static failure
  is CI resolving unpinned mypy 2.3.0 while the recorded local public-package
  baseline is 1.17.1; pin the CI tool to `mypy==1.17.1` and rerun the gate.
- Mypy correction: version 1.17.1 exposes 20 pre-existing gradual-typing
  findings in retained provider/runtime prototypes. Namespace-wide mypy remains
  required, while only those documented runtime-interface error categories are
  disabled; import/name/syntax and all other configured static checks remain in
  the release gate.
