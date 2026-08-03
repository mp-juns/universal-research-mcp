# WORK_LOG

## 2026-08-04 — real multi-agent runtime (planned)

- Requested and decided by: User.
- Planned executor: Codex with independent read-only architecture, provider,
  and role-prompt audits before implementation.
- Authority: implement every feasible runtime layer in this repository and
  activate the validated local package/plugin. Do not claim access to private
  host schedulers or use a provider/model without explicit configuration,
  approval, and budgets.
- Planned operations: inspect the supplied governance specifications and current
  execution/provider/session contracts; add eleven prompt packs, isolated
  session storage, real provider-backed orchestration, local loopback generation,
  CLI/MCP execution surfaces, schemas, tests, documentation, and plugin updates;
  then run local fixtures, full tests, wheel/plugin validation, and installed
  smoke tests.
- Estimated scope: 40–60 reads, 25–40 modified files, 35–60 tests; high
  difficulty; USD 0 and no model, API, benchmark, network, or visualization run.
- Stop conditions: secret exposure, unapproved network/model use, session or
  canonical-ledger corruption, a worker executing before the scope governor,
  cross-agent context leakage, hidden retry, budget bypass, or failure to block
  an out-of-scope operation.

### Completed implementation and verification

- Implemented eleven complete, versioned role prompt packs and bound every
  session to the exact task, prompt pack, evidence bundle, plan, provider
  configuration, estimate snapshot, and execution-request hashes.
- Implemented governor-first execution with one plugin-owned immutable session
  and one provider call per activated role, followed by bounded parallel worker
  calls. These sessions are intentionally distinct from native Codex/ChatGPT/
  Claude GUI tasks and do not claim access to a private host scheduler.
- Added explicit local-loopback OpenAI-compatible generation and explicit
  OpenAI/Anthropic generation routing. No credential value is accepted in an
  MCP argument, and no local or remote model is started automatically.
- Moved one-time execution grants to project-external host state partitioned by
  project-root hash. Approval now binds plan, exact cost/token reservations,
  provider/model/configuration, network scope, budgets, and expiry; consumption
  is recorded before provider dispatch.
- Added deterministic scope-governor cross-field enforcement, exact provider
  model pinning, mandatory exact-bundle citation for source-required passing
  decisions, and summary-only user inspection that excludes provider-authored
  prose and raw artifacts.
- Hardened evidence and runtime storage with canonical-record validation,
  completed-amendment resolution, withdrawal/supersession blocking, exact
  source registration, `openat`/`O_NOFOLLOW` reads, directory-handle anchoring,
  immutable artifacts, ledger seals, and file/event/session size limits.
- Preserved failure occurrence as a minimum tombstone while retaining the
  configured `full|metadata_only|ask` detail choice and safe-stop policy.
  Host visualization remains a separate capability that is off by default.
- Full local regression: 235 passed and 2 skipped. Ruff and diff whitespace
  checks passed. No provider, model, network, benchmark, or visualization call
  occurred.
- Built and validated `dist/universal_research_mcp-0.3.0-py3-none-any.whl`
  (SHA-256 `993e56957020faab3c7873e60410b0324e82d250a0a3aa45127fbe0002464aa9`).
  A repository-external venv loaded version 0.3.0 from site-packages, initialized
  fresh lexical and empty semantic databases, and loaded all eleven role packs.
- One smoke assertion initially used obsolete `registry_report()` keys and
  raised `KeyError`; inspection showed the documented `role_count`/`issues`
  contract, and the corrected installed-package assertion passed.
- Installed the verified 0.3.0 wheel into the user environment and reinstalled
  the local Codex plugin as
  `0.3.0+codex.20260803223419`. The installed plugin and both Skills validated,
  and an unapproved runtime call remained blocked before provider setup.
- Remaining trust boundary: file-backed grants and unkeyed local seals are
  tamper-evident, but a process with the same OS identity and host-state write
  access can recompute them. Cryptographic proof of live human presence would
  require an OS-backed signing key or separately privileged host approval
  broker, which this plugin does not claim to provide.

## 2026-08-04 — installed 0.2.0 bounded end-to-end test (completed)

- Requested and approved by: User (`한번 테스트 작업해봐`).
- Planned executor: Codex; three host subagents may inspect contracts read-only
  and independently, but may not edit or execute the temporary test project.
- Test artifact scope: a disposable project under `/tmp` with one synthetic
  note, one canonical event, and only regenerated derived databases.
- Planned checks: scope/cost preflight, fresh initialization, append-only event
  validation, lexical and empty-semantic health, candidate search, exact
  line/hash evidence fetch, and deterministic rejection of a deliberately
  unapproved action.
- Expected cost: USD 0; no paid call, model run, network transfer, benchmark,
  visualization, or persistent project data.
- Stop conditions: schema rejection, unhealthy/stale index, unexpected external
  capability request, source/hash mismatch, or failure to block the deliberate
  scope violation.

### Results and independent verdict

- Verified from outside the source tree that both the installed module and
  console entry point are version 0.2.0 and load from the user site-packages
  installation.
- Initialized a disposable project under
  `/tmp/universal-research-task-test.aIibu3/project`. Added one schema-valid
  Core claim and one registered synthetic source containing a unique retrieval
  sentinel; no persistent project ledger was changed.
- The scope/cost packets, synthetic passing governor decision, bound receipt,
  non-executing dispatch manifest, and USD 0 harness preflight all validated.
  The approved local search operation received `allow_tool_call`; the same
  operation with `private/private.txt` received `reject_tool_call`, workflow
  `blocked`, reapproval required, and `GOV-SCOPE-001`.
- Automatic lexical refresh produced one event and one eligible source passage,
  passed FTS parity and canonical retrieval verification, and remained current
  with SQLite integrity `ok`.
- A deterministic, model-free fixture embedder exercised only semantic artifact
  creation. The semantic store is current with one event, one passage, three
  dimensions, matching DB/health hashes, successful self-retrieval, and exact
  canonical source-slice re-fetch. This does not prove query-time semantic or
  hybrid product search.
- The installed MCP module returned one lexical candidate for the unique
  sentinel. Exact candidate `event_id`, path, line range, and SHA-256 were passed
  unchanged to the evidence fetch; indexed, expected, and current hashes all
  matched and the verified content contained the sentinel. Query-time semantic
  mode was explicitly rejected rather than reported as an empty success.
- Three independent read-only reviews passed the lexical provenance, semantic
  artifact integrity, and governance dry-run contracts. The combined verdict is
  a bounded memory E2E plus governance dry-run pass, not proof of autonomous
  multi-agent execution, host-side enforcement, semantic ranking quality, or
  remote provider operation.

### Unexpected result and limitation

- The synthetic event used `2026-08-04T12:00:00+09:00`, while the verified local
  time was `2026-08-04T05:47:57+09:00`. The record was therefore future-dated.
  This does not invalidate the isolated retrieval/hash test, but it exposed that
  the current read-only audit validates record structure and selected policy
  rules without flagging a completed canonical record whose timestamps are in
  the future. The returned audit finding count of zero is technically consistent
  with current code but should not be interpreted as temporal-integrity proof.
- The current Codex task retained its pre-install plugin capability snapshot and
  its registered 0.1.0 Skill path disappeared when the cache was replaced. The
  installed 0.2.0 Skill instructions and installed CLI/MCP module were therefore
  used as the fallback. A new Codex task is still required to test the refreshed
  host-loaded plugin transport and both 0.2.0 Skills.
- No external API/model call, credential access, network, dependency or model
  download, benchmark, visualization, background worker, remote publication, or
  source-repository canonical research mutation occurred.

## 2026-08-04 — 0.2.0 package and plugin activation (completed)

- Requested and approved by: User (`ㄱㄱ`).
- Planned executor: Codex.
- Authority: install the already verified local 0.2.0 wheel into the user Python
  environment and reinstall the existing local marketplace plugin through the
  Codex CLI. Do not edit marketplace JSON by hand.
- Validation: installed command versions, plugin version/cache path, plugin and
  Skill validation, and a temporary fresh-project initialization.
- Excluded: remote publication, network dependency download, provider/API/model
  execution, credentials, benchmark, visualization, and canonical research
  mutation.

### Results and verification

- Rebuilt the local 0.2.0 wheel without dependency download and passed the
  distribution validator before activation.
- Replaced the user-environment 0.1.0 package with 0.2.0. Both management and
  MCP console entry points report 0.2.0, and the installed governance registry
  validates as `agent-governance/2.0` with exactly eleven roles.
- Reinstalled and enabled `universal-research-memory` from the existing local
  marketplace. The active cache is
  `0.2.0+codex.20260803200015`; the installed plugin and both installed Skills
  passed their validators.
- Initialized a fresh temporary project from the installed package. It created
  lexical and semantic SQLite stores plus both health records. Diagnostics
  report both indexes current, non-stale, and structurally healthy; the empty
  semantic store intentionally remains provider/model unconfigured.
- Confirmed the installed unified MCP exposes twenty memory, governance,
  provider, and index-status tools. Legacy duplicate `research_*` search aliases
  remain hidden by default.
- A supplementary path check initially assumed the eleven-role registry would
  be copied into every initialized project. Initialization intentionally keeps
  that registry in the installed support bundle; the authoritative installed
  registry validation and fresh-project diagnostics both passed.
- No provider/API/model call, credential access, dependency download, network
  publication, benchmark, visualization, canonical research write, GitHub push,
  or PyPI publish occurred.

## 2026-08-04 — unified plugin and eleven-role governance (planned)

- Requested and decided by: User
- Planned/executed by: Codex
- Independent read-only audits: DB/index path, plugin/CLI packaging, and provider
  security were delegated; they did not edit, test, download, or use network.
- The user confirmed the GitHub/PyPI source is already in this directory, so no
  repository download or remote source merge will occur.
- The initial planning label `scope_plan_governor` was superseded by the user's
  final `scope_and_cost_governor` name and a breaking `agent-governance/2.0`
  roster contract. It runs before plan approval and monitors every operation or
  scope change; it may issue findings but cannot approve, execute, or kill work.
- Failure policy is two-dimensional: stop `always|blocking_only|current_step`,
  record `full|metadata_only|ask`, and detail `full|redacted|hashes_only`.
  Defaults are `blocking_only + ask + redacted`; precedence is task packet,
  project profile, environment, then defaults. A minimum stopped-work trace is
  retained, and fully ungoverned/unrecorded runs are claim-ineligible.
- API keys are never accepted as MCP/chat arguments. CLI configuration accepts
  only environment-variable or optional OS-keyring references, never a key
  value. Tests use fake providers and no network/cost.
- Host visualization Skills/capabilities in ChatGPT or Claude Code default to
  off and require explicit user opt-in plus task scope. This is distinct from
  approved `data_plot_generation` through ordinary research code such as
  matplotlib.

### Results and verification

- Upgraded the registry to `agent-governance/2.0` with eleven roles. A normal
  or critical dispatch now requires a deterministic receipt produced from a
  validated passing `scope_and_cost_governor` decision and bound to every exact
  task-packet and scope hash. The parallel harness records this receipt before
  submitting workers.
- Added mandatory per-task `estimated_cost_usd`, maximum and aggregate cost
  checks, bounded parallelism, and provider call/input/output/cost reservations.
  Omitted costs are no longer silently treated as zero and no hidden retry is
  performed.
- Implemented failure classification, immediate stop directives, mandatory
  minimum tombstones, `blocking_only + ask + redacted` defaults, environment
  overrides, and pending user-choice reporting. The policy requests host-owned
  shutdown actions but does not claim force-termination authority.
- Kept ChatGPT/Claude host visualization off by default and independent from
  ordinary data-plot permission. It requires task capability scope, a plan
  reference, explicit user opt-in, and the exact approved scope hash.
- Added fresh-project initialization plus staged, verified, atomic lexical and
  semantic index promotion. Both derived indexes emit schema-valid health
  records. Semantic failure preserves a usable prior DB as stale, sanitizes the
  failure record, and verifies canonical event/source retrieval.
- Added secret-free provider configuration, cached-local embedding, OpenAI
  embedding/generation, Anthropic generation, HTTPS host allowlists, explicit
  per-run remote approval and budgets, and terminal no-cross-provider retry.
  No key value is accepted through chat, command arguments, or config.
- Unified memory, governance, index/provider status, and parallel preflight in
  one MCP and management CLI. Legacy `research_*` aliases are opt-in only.
  Query-time semantic/hybrid search is not exposed until a governed encoder and
  ranker are connected; it never returns an empty result as false success.
- Addressed the public-install review: `init`/index management, indexed-source
  fetch allowlisting, exact event/hash verification, PyPI URLs and 0.2.0
  metadata, schema/pack/plugin wheel bundle, complete CI/publish gates, and a
  clean-wheel initialization smoke are implemented. Namespace migration and a
  bilingual README remain explicit later compatibility work.
- Verification: full suite `132 passed, 2 skipped`; the skips retain legacy
  project/code-search fixtures whose implementations were intentionally not
  migrated. `ruff check .` and `git diff --check` passed. A fresh 0.2.0 wheel
  was built without dependency download, its support bundle validated,
  installed into an isolated environment, and used to initialize healthy empty
  lexical and semantic stores and run diagnostics.
- Plugin cachebuster was refreshed after the final Skill edits; the plugin and
  both Skills validated. The active Codex plugin cache and globally installed
  PyPI package were not replaced because installation and marketplace mutation
  were outside this run's approved mutation scope.
- No live provider/API call, credential entry, model load/download, benchmark,
  background worker, canonical research append, GitHub push, PyPI publish, or
  marketplace mutation occurred.

## 2026-08-04 — Codex dispatch-manifest handoff (planned)

- Requested by: User with `ㄱㄱ` after the safe host-adapter handoff report.
- Build a no-write export/capture surface for validated dispatch requests. It
  does not invoke Codex's private scheduler, select a model, or run any agent;
  the resulting manifest is the portable boundary that a Codex host may execute.
- Validate only through local fixtures, CLI, Skill/plugin checks, and a plugin
  cachebuster. Exclude all execution, ledger/data writes, network, and release.

### Results and verification

- Added deterministic dispatch-manifest serialization plus `urgov dispatch`
  prepare, critical-batch, and capture commands. They print or validate data;
  they do not write a project artifact or execute a host worker.
- Added corresponding read-only governance MCP tools for one dispatch, isolated
  critical batch construction, and returned decision capture.
- Extended adapter fixtures to verify deterministic export and reject a
  non-dispatchable manifest.
- Codex adapter fixtures: 5 passed. URAG governance fixtures: 6 passed. The
  governance MCP host-handoff surface, governance Skill, and plugin manifest
  also validated successfully. Plugin cachebuster was updated locally only.

## 2026-08-04 — Codex host-adapter foundation (planned)

### Ownership and boundary

- Requested by: User
- Planned and executed by: Codex
- Verified by: Pending local fixture checks
- Approval evidence: User approved the Phase 2 Codex adapter step with `ㄱㄱ`.
- Official Codex-manual lookup was attempted before implementation but the
  environment could not resolve `developers.openai.com`. The adapter therefore
  relies only on verified current-session capability boundaries: host-side
  parallel agents exist, but they are not exposed as a callable plugin/MCP API.

### Planned work

- Build a local adapter that renders validated URAG packets into dispatch
  requests for the Codex host and validates returned decision records.
- Preserve critical-reviewer evidence snapshot equality and result isolation.
- Do not dispatch a native agent, select a model, call an API, create a ledger
  record, or execute a command from the adapter. Those remain host/user actions
  after a separate task authorization.

### Results and verification

- Added `integrations.codex.adapter`. It maps a valid URAG task packet to a
  host-owned, role-scoped dispatch request; no adapter field grants model,
  network, write, or scheduler authority.
- Added isolated critical-review batch construction. It requires each of the
  four reviewers exactly once and one identical non-empty evidence snapshot.
- Added decision capture: malformed output is returned as an auditable invalid
  artifact with a bounded one-repair recommendation and is never promoted to a
  finding or conclusion.
- Updated the governance Skill and host-integration documentation. Updated the
  plugin cachebuster without changing its marketplace registration.
- `python3 -m unittest discover -s tests -p 'test_codex_adapter.py'`: 4 passed.
- URAG governance (6) and prior governance (8) checks, Skill validation, and
  plugin validation also passed.

## 2026-08-04 — URAG v1 Phase 0–1 optimization (planned)

### Ownership

- Requested by: User
- Idea proposed by: User
- Decision made by: User
- Planned by: Codex
- Executor: Codex
- Verifier: Pending local contract checks
- Approval evidence: User supplied the URAG v1 detailed specification and
  requested optimization for this environment.

### Plan and environment decisions

- Keep all inference host-provided. The local plugin will not call a model API,
  create a billing bypass, or start a model worker; Codex/Claude/ChatGPT remain
  adapters that supply their own authorized inference.
- Make the governance controller deterministic, local, and read-only at the
  MCP boundary. Add a separate future write boundary only after task-specific
  approval; do not expose it in this phase.
- Implement manifest-backed role registry, canonical hashing, packet/decision
  validation, workflow state transitions, escalation, read-only CLI/MCP, and
  local fixtures. The expected commands are the focused local tests, CLI
  validation calls, Skill/plugin validation, and plugin cachebuster update.
- Exclude network, package install, daemon start, model/embedding/index work,
  experiment/benchmark execution, project ledger writes, remote host deployment,
  marketplace changes, and publication.

### Results

- Added the dependency-free `governance/` package. Its registry loads exactly
  ten manifest-backed roles, computes canonical SHA-256 hashes, and fails closed
  if the roster, authority, or critical-reviewer boundary differs from policy.
- Added hash-bound `research-agent-task/1.0` and
  `research-agent-decision/1.0` validation, scope/approval/evidence checks,
  deterministic workflow transitions, and non-LLM escalation/gate evaluation.
- Added `urgov` diagnostics plus a read-only governance MCP. Both validate and
  report only; neither starts a worker, calls an external model, writes a
  ledger, runs a command, nor rebuilds an index.
- Updated the local Codex plugin to launch the existing research-memory MCP and
  the new governance MCP. Updated its cachebuster; no marketplace registration
  or remote change was made.
- Added local-first v1, security, host-integration, workflow-mode, and
  role-authority documents. Claude Code and ChatGPT are deliberately expressed
  as future adapters using the same contracts, not as new model backends.

### Unexpected result and correction

- The first `python -m governance.cli registry validate` check returned without
  output because the module lacked a `__main__` entry point. The installed
  console-script entry point was unaffected, but the module path was corrected
  and the complete approved check set was rerun successfully.

### Verification

- `python3 -m unittest discover -s tests -p 'test_urag_governance.py'`: 6 passed.
- `python3 -m governance.cli registry validate`: passed; registry reports ten
  roles and no integrity issues.
- `python3 -m unittest discover -s tests -p 'test_governance.py'`: 8 passed.
- `python3 -m unittest discover -s tests -p 'test_contract_files.py'`: 2 passed.
- `python3 -m unittest discover -s tests -p 'test_framework_operations.py'`: 6 passed.
- Governance Skill and the updated Codex plugin validated successfully.

## 2026-08-04 — governed multi-agent research foundation (planned)

### Ownership

- Requested by: User
- Idea proposed by: User
- Decision made by: User
- Planned by: Codex
- Executor: Codex
- Verifier: Pending local fixture validation
- Approval evidence: User asked to build the governed research plugin after
  supplying the ten-role governance and central-manager specifications.

### Planned work and boundaries

- Add a dependency-free governance layer for the exact fixed ten-agent roster,
  Lightweight/Benchmark/Final-review activation modes, authority checks,
  machine-readable task and decision records, review escalation, and concise
  user-chat reporting.
- Add a derived-index refresh *eligibility* and health-record contract.  It
  does not rebuild an index or append a canonical research event in this pass.
- Add local fixtures and documentation.  Planned validation is limited to the
  new governance tests, existing contract/framework tests, and local plugin and
  Skill validators.
- Excluded: reference-project access, canonical-data mutation, derived-index
  build, model or embedding load, package install, network, remote execution,
  daemon/background worker, benchmark, experiment, marketplace, and release.

### Results

- Added the dependency-free `core.governance` role registry for the exact
  ten-agent roster. It validates task scope, role/mode activation, decision
  records, evidence-addressable findings, authority use, claim blockers, and
  the central manager's summary-only chat envelope.
- Added `core.index_refresh` to decide whether a *recorded* research event may
  trigger a derived-only index refresh and to validate a retrievable index-health
  result. No project index was built or changed.
- Added versioned task, decision, and index-health schemas; governance fixtures;
  architecture and operations documentation; and the `research-governance`
  plugin Skill.
- A first governance test run exposed only an assertion-text mismatch for the
  expected failed retrieval verification. The validator correctly rejected the
  health record; the fixture expectation was corrected before the full approved
  validation rerun.

### Verification

- `python3 -m unittest discover -s tests -p 'test_governance.py'`: 8 passed.
- `python3 -m unittest discover -s tests -p 'test_contract_files.py'`: 2 passed.
- `python3 -m unittest discover -s tests -p 'test_framework_operations.py'`: 6 passed.
- `python3 <skill-creator>/scripts/quick_validate.py
  plugin/universal-research-memory/skills/research-governance`: passed.
- `python3 <plugin-creator>/scripts/validate_plugin.py
  plugin/universal-research-memory`: passed.

## 2026-08-03 — universal research MCP bootstrap

### Ownership

- Requested by: User
- Idea proposed by: User
- Decision made by: User
- Planned by: Codex
- Executed by: Codex
- Verified by: Pending user-requested migration handoff; no tests run
- Approval evidence: User explicitly requested `이주시작해 README에 뭘할건지 정의하고`

### Attribution

- User contribution: Defined the separation boundary, read-only reference policy, and goal of generalizing the MCP/plugin.
- Codex contribution: Created the independent project scaffold, README definition, generic operating documents, and migration plan.
- External contribution: None used.
- Existing work preserved: Original project and its research DBs remain untouched.
- Not performed by Codex: No experiment, benchmark, network, background watcher, remote task, or source-project mutation.

### Initial commands

```text
mkdir -p <workspace-root>
python3 <codex-skill-root>/plugin-creator/scripts/create_basic_plugin.py universal-research-memory --path <workspace-root>/plugin --with-mcp --with-skills --with-scripts
mkdir -p agents db/schema db/reference mcp/project_search scripts requirements tests data/events data/index .codex-plugin
mkdir -p config
```

### Results

- Created `<workspace-root>`.
- Created plugin scaffold at `plugin/universal-research-memory`.
- Added independent README, AGENTS, agent rules, TODO, and WORK_LOG.
- Source-code and schema-copy phase is pending in this task.

### Migration execution

The first relative-path copy attempt failed because the command ran with the new project as `cwd` while referring to `tools/project_search/...` as though it were still in the source project. Exit output was `cp: cannot stat ...`; no source file was modified and no intended source file was copied.

The corrected foreground copy used absolute source paths and copied:

- `tools/project_search/*.py`, JSON/config files, requirements, and static placeholder into `mcp/project_search/`
- research ledger, semantic index, watcher, device, correction, and reference-corpus scripts into `scripts/`
- project-search and research-memory requirements into `requirements/`
- focused MCP/index tests into `tests/`

The copied MCP defaults were then changed to use this project's `data/index/` paths. The plugin manifest and `.mcp.json` were configured for the independent MCP path. Existing SQLite files and embedding artifacts were not copied.

### Plugin validation

- First validation attempt: failed before process creation because the supplied `cwd` did not exist; no project file was affected.
- Correct command: `python3 <codex-skill-root>/plugin-creator/scripts/validate_plugin.py <workspace-root>/plugin/universal-research-memory`
- Result: `Plugin validation passed`.

## 2026-08-04 — generic runtime adapter separation

### Ownership

- Requested by: User
- Idea proposed by: Codex in the previous handoff
- Decision made by: User
- Planned by: Codex
- Executed by: Codex
- Verified by: Pending source-level handoff; no server/test execution planned
- Approval evidence: User replied `ㅇㅇ ㄱㄱ` to the generic adapter next step.

### Attribution

- User contribution: Approved continuing the independent generic MCP/plugin project.
- Codex contribution: Planned the adapter boundary and will implement the new runtime configuration layer.
- External contribution: None.
- Existing work preserved: Original research project, its DBs, and its logs remain untouched.
- Not performed by Codex: No index rebuild, event append, server run, test, benchmark, network, background, or remote work.

### Planned mutation

- Add `mcp/project_search/runtime_config.py`.
- Change `mcp/project_search/server.py` and `mcp/project_search/mcp_server.py` to consume generic runtime settings.
- Update `config/profile.yaml` and README adapter documentation.

## 2026-08-03 — universal research framework implementation

### Ownership

- Requested by: User
- Idea proposed by: User
- Decision made by: User
- Planned by: Codex
- Executed by: Codex
- Verified by: Codex through fixture and compatibility tests; user review pending
- Approval evidence: User explicitly approved implementation with `ㄱㄱ 구현해` and remaining scope with `ㅇㅇ 이제 다하십쇼 나머지`.

### Scope and exclusions

- Implemented only in this independent project: core schemas, ledger validation,
  amendment/proposal/audit/view adapters, local read-only MCP, plugin Skill,
  fixtures, tests, and documentation.
- Did not access or modify the reference project, historical result/log/DB,
  existing indexes, model artifacts, remote services, or marketplaces.
- Did not install dependencies, create a derived index, load an embedding model,
  or start a daemon.

### Results

- Added versioned Core, Pack, and Project Profile schema contracts.
- Added a dependency-free validator that supports current legacy event records.
- Added narrow, fail-closed resolved amendment views that never modify canonical
  input and an approval-gated append helper not exposed through MCP.
- Added read-only audit findings and Markdown plan/work-log projections.
- Made lexical and semantic index builders reject invalid canonical input before
  creating a derived index.
- Replaced the plugin's daemon proxy configuration with a local, read-only
  lexical/evidence/audit MCP; semantic retrieval remains an optional derived
  adapter and not evidence.

### Verification commands and outcomes

- `python3 -m unittest discover -s tests -p 'test_core_ledger.py'`: 4 passed.
- `python3 -m unittest discover -s tests -p 'test_research_memory_mcp.py'`: 2 passed.
- `python3 -m unittest discover -s tests -p 'test_contract_files.py'`: 2 passed.
- `python3 -m unittest discover -s tests -p 'test_framework_operations.py'`: 4 passed.
- `python3 -m pytest -q tests/test_build_research_ledger_index.py`: 4 passed.
- `python3 -m pytest -q tests/test_build_research_semantic_index.py`: 15 passed.
- `python3 <codex-skill-root>/skill-creator/scripts/quick_validate.py plugin/universal-research-memory/skills/research-workflow`: passed.
- `python3 <codex-skill-root>/plugin-creator/scripts/validate_plugin.py plugin/universal-research-memory`: passed.
- Applied the local plugin cachebuster `0.1.0+codex.20260803135458` and reran
  plugin validation successfully. No marketplace entry was created or changed.

### MCP runtime verification

- User explicitly approved installing the documented MCP runtime for transport
  verification. Installed `mcp[cli]>=1.27,<2`, resolving to `mcp 1.29.0`.
- Loaded `mcp/research_memory/mcp_server.py` without starting a server or
  touching a ledger/index. Registered tools: `memory_search_candidates`,
  `memory_latest`, `memory_fetch_evidence`, `memory_audit_ledger`, and the
  compatibility aliases `research_search`, `research_latest`, `research_fetch`.
- No marketplace entry, reference-project file, historical DB/log, derived
  index, model, or remote research service was changed.

## 2026-08-04 — fixture E2E and local marketplace installation

### Ownership

- Requested by: User
- Planned and executed by: Codex
- Verified by: Codex; GitHub publishing remains blocked by missing repository
  and expired GitHub CLI authentication.

### E2E verification

- Added an isolated temporary fixture that creates its own canonical JSONL and
  derived lexical SQLite index, then verifies MCP candidate search, source-line
  fetch, and read-only audit. No project ledger or index was created.
- Closed SQLite connections explicitly in lexical and semantic index builders
  after an E2E `ResourceWarning` exposed the previous lifecycle gap.
- Checks passed: E2E unittest, lexical builder pytest (4), and semantic builder
  pytest (15).

### Plugin marketplace

- The workspace top-level `.agents` directory is read-only, so the supported
  marketplace layout lives under `marketplace_root/.agents/plugins/`.
- Registered marketplace `universal-research-local` with Codex and installed
  `universal-research-memory` version `0.1.0+codex.20260803135458`.
- Removed the temporary incorrect `marketplace/` directory created during the
  failed manifest-layout attempt; it contained only the generated manifest and
  symlink, no research data.
- `codex plugin list` confirms the plugin is installed and enabled.

### GitHub blocker

- `<workspace-root>` was not a Git repository and had
  no remote.
- `gh auth status` reports the active GitHub token is invalid. No GitHub or
  remote change was attempted.

## 2026-08-04 — public GitHub publication

### Ownership

- Requested by: User
- Planned and executed by: Codex
- Repository: `https://github.com/mp-juns/universal-research-mcp`

### Publication

- Initialized this independent workspace as a new Git repository on `main`.
- Added a public-release `.gitignore` for workspace-managed directories,
  caches, Python bytecode, and derived index artifacts.
- Replaced local absolute paths in public documentation, profile, and example
  configuration with portable placeholders before committing.
- Created initial commit `9922b7b` (`Add universal research operations
  framework`) and pushed it to public `origin/main`.
- A draft PR was not created because this is the initial commit on the new
  repository's default branch.

### Verification

- Core, framework, and E2E unittest checks passed (9 tests total).
- Derived lexical/semantic builder regression checks passed (19 tests total).
- Research workflow Skill and Codex plugin manifest validation passed.
- Final branch state after initial push: `main...origin/main` with no pending
  changes at that point.

### Unexpected verification handling

- The direct `pytest` launcher did not include the workspace root in import
  resolution, so it could not find `scripts`. The test was rerun with
  `python3 -m pytest`, which passed. No source or data was changed as a result.
- The development environment lacks the optional MCP Python runtime. Pure core
  tests therefore do not import MCP transport; no dependency was installed.

## 2026-08-04 — review P0 plan: Core retrieval and approval hardening

### Ownership and scope

- Requested by: User
- Planned and executed by: Codex
- Decision: implement only the review's P0 integration and integrity findings;
  defer package/distribution, license, and full schema-validator parity work.

### Planned changes

- Add a derived-only Core-to-index projection that maps `record_id`,
  `record_kind`, `source_refs`, and `relations[].target_id` into the existing
  lexical/semantic document contract without altering canonical JSONL.
- Resolve Core amendments before indexing, alongside existing legacy correction
  handling.
- Require an existing `approval` record that is approved, human-issued, and
  explicitly scoped before the append helper accepts a record.
- Have evidence fetch report indexed and current SHA-256 values plus a bounded
  integrity status.

### Verification plan

- Run the core, framework, Core MCP E2E, lexical-builder, and semantic-builder
  fixture tests. The tests use temporary directories only and do not create or
  modify the project ledger or derived indexes.

### Results

- Added `core/indexing.py`; its projection is derived-only and keeps the Core
  record itself as the `raw_json` authority in SQLite.
- Both index builders now distinguish Core from legacy input, apply the
  appropriate append-only correction mechanism, and project Core fields before
  retrieval indexing.
- `append_approved_record` now rejects nonexistent, non-approval, non-approved,
  AI-issued, unscoped, and out-of-scope approval references.
- `memory_fetch_evidence` now reports `indexed_sha256`, `current_sha256`, and
  `integrity_status`; `sha256` remains a compatibility alias for the current
  file hash. Read-only SQLite connections now close explicitly.
- The E2E fixture uses a Core claim and protocol, verifies the relational
  projection and raw Core provenance, confirms a matched hash, then mutates its
  temporary source and confirms `mismatched` status.

### Verification outcomes

- `python3 -m unittest discover -s tests -p 'test_core_ledger.py'`: 4 passed.
- `python3 -m unittest discover -s tests -p 'test_framework_operations.py'`: 6 passed.
- `python3 -m unittest discover -s tests -p 'test_research_memory_e2e.py'`: 1 passed.
- `python3 -m pytest -q tests/test_build_research_ledger_index.py tests/test_build_research_semantic_index.py`: 20 passed.
- No warnings remained after explicitly closing MCP read-only SQLite
  connections. No reference-project, canonical-project-ledger, historical-log,
  network, or daemon operation occurred.

## 2026-08-04 — public usability and contract parity plan

### Ownership and scope

- Requested by: User, continuing the public-project review follow-up.
- Planned and executed by: Codex.
- Scope: packaging and launcher independence, JSON Schema/manual validator
  parity coverage, a public license, Quick Start, and local plugin refresh.
- Excluded: all reference-project activity, research-data writes, model or
  benchmark execution, remote research services, and write-capable MCP tools.

### Planned verification

- Exercise only package help/version entry points and fixture tests.
- Validate and refresh the existing local plugin through its documented
  cachebuster/reinstall flow after changing its MCP configuration.
- Scan the commit for local paths and credential patterns before the
  user-authorized push.

### Results

- Added installable `universal_research_mcp` runtime and the
  `universal-research-mcp` console entry point. It accepts explicit root,
  lexical-index, and event-root paths and retains a source-tree compatibility
  wrapper for existing callers.
- Added `pyproject.toml` with Python 3.11+, read-only MCP runtime dependency,
  and optional test dependencies; added MIT `LICENSE`.
- Replaced the plugin's `../../mcp/...` and `../../data/...` configuration with
  the stable console entry point. Project routing now comes from the launch
  working directory or explicit environment/CLI configuration, not a plugin
  parent path.
- Expanded the dependency-free validator to mirror schema-declared ID, ISO
  date-time, additional-property, relation, reference-array, actor, and
  evidence constraints. A JSON Schema parity fixture compares acceptance of
  representative valid and invalid contracts.
- Added local MCP safety coverage for sensitive path rejection and symlink
  escape rejection.
- Updated the existing plugin cachebuster to
  `0.1.0+codex.20260803165050`, validated it, and reinstalled it from the
  already configured `universal-research-local` marketplace.

### Verification outcomes

- `python3 -m universal_research_mcp --help` and `--version`: passed; version
  is `0.1.0`. `uv` is not installed in this environment, so no `uv run`
  command was executed or installed.
- Core Schema parity: 5 unittest checks passed.
- MCP path safety: 2 unittest checks passed.
- Core candidate → evidence → audit E2E: 1 unittest check passed.
- Approval boundary: 6 unittest checks passed.
- Public distribution contract: 2 unittest checks passed.
- Derived lexical/semantic index regression: 20 pytest checks passed.
- No reference-project, historical-log, canonical-project-ledger, model,
  benchmark, daemon, or remote research operation occurred.

## 2026-08-04 — retrieval failure-mode hardening

### Ownership and scope

- Requested by: User, continuing the public-project review follow-up.
- Planned and executed by: Codex.
- Scope: fixture-only failures around source mutation, malformed JSONL, and a
  large derived-index input; no project data or reference project was used.

### Results and verification

- `memory_fetch_evidence` reads a source artifact into one byte snapshot before
  both rendering its line range and calculating `current_sha256`. A replacement
  after that read cannot produce a response whose displayed content and hash
  describe different file versions.
- Added an explicit malformed-JSONL rejection fixture and a 1,000-record
  lexical-index fixture that asserts canonical input bytes remain unchanged.
- MCP safety (2) and Core E2E (1) unittest checks passed; lexical/semantic
  builder regression checks passed (22). No warning, project-data mutation,
  reference-project access, network, model, daemon, or benchmark occurred.

## 2026-08-04 — local package installation verification plan

- User explicitly requested continuation after the `uv` availability report.
- The only environment mutation is a user-local, no-dependency installation of
  this public package to make the plugin's stable console command available.
- Verification is limited to `--help` and `--version`; the MCP transport is not
  started and no research root is read or written.

### Results

- User explicitly approved and completed `python3 -m pip install --user
  --no-deps .`; the local package installed as `universal-research-mcp 0.1.0`.
- The console entry point resolved from the user PATH and both `--version`
  (`0.1.0`) and `--help` passed. It was not used to start MCP transport.
- The build frontend prepared an isolated local wheel as part of normal
  packaging. No runtime dependency was requested through this command, and no
  research project, data, reference project, model, benchmark, daemon, or
  remote research service was touched.

## 2026-08-04 — public CI baseline plan

- Requested by: User, continuing the public-project hardening work.
- Scope: add a minimal GitHub Actions workflow for package/core/read-only MCP
  and lexical fixture tests only.
- Explicitly excluded: registry publishing, secrets, deployment, model loading,
  semantic encoder dependencies, benchmark, reference project, and research
  data.

### Results and verification

- Added `.github/workflows/ci.yml` with least-privilege read permissions and a
  Python 3.11 job on pushes and pull requests.
- The job installs the project with its test extra, checks the console entry
  point, then runs contract, core, approval, public-distribution, read-only
  MCP, MCP E2E, and lexical-index fixtures. It deliberately does not install or
  run the semantic encoder stack.
- The identical local baseline passed: entry point version `0.1.0`; unittest
  groups 2, 5, 6, 2, 2, 2, 2, and 1 passed; lexical pytest group 6 passed.

### CI compatibility correction

- The first public CI run failed only in the Schema parity fixture: the runner's
  JSON Schema implementation treated the draft `format` vocabulary differently
  from the local default checker.
- The test now supplies an explicit `FormatChecker(formats=["date-time"])`,
  making date-time assertion independent of validator-default behavior. The
  test extra explicitly requests `jsonschema[format]` so CI installs the
  date-time format provider. The lexical fixture also needs `numpy` through its
  query helper, so the test extra declares it without adding the semantic
  encoder stack. The focused local Core test passed (5 checks). The next push
  reruns CI.

### Final CI outcome

- The corrected public workflow completed successfully: all package install,
  entry-point, contract, core, approval, read-only MCP, MCP E2E, and lexical
  fixture steps passed in the GitHub Actions `core-contracts` job.
- GitHub emitted only a hosted-runner Node.js deprecation notice for the
  upstream checkout/setup actions; it did not affect the Python workflow or
  test result.

## 2026-08-04 — optional semantic runtime contract

- Requested by: User, continuing public-project completion work.
- Added a `semantic` package extra for the already-present, optional semantic
  index builder dependencies. The default MCP dependency set remains lexical
  and read-only.
- No extra was installed; no model was downloaded or loaded; no semantic index,
  benchmark, project data, or reference project was touched.

## 2026-08-04 — PyPI Trusted Publishing release plan

- Requested by: User, who confirmed PyPI Trusted Publishing is configured.
- Plan: add a release-published-only workflow with build artifact verification
  and PyPI OIDC publishing, push it, then create the initial `v0.1.0` GitHub
  Release matching package metadata.
- No token will be read, copied, committed, or printed. The workflow receives
  only `contents: read` and `id-token: write` permissions.

### Results and verification

- Added the release-published-only PyPI workflow, pushed it, and created the
  initial public `v0.1.0` GitHub Release.
- The Trusted Publishing workflow completed successfully: source/wheel artifacts
  built, `twine check` passed, PyPI publish passed, and digital attestations
  were generated. No PyPI API token was handled by this workspace.
- No reference project/data, model, semantic index, benchmark, daemon, or MCP
  write capability was involved.

## 2026-08-04 — consolidated operating specification plan

- Requested by: User.
- Plan: author one standalone Markdown specification of the implemented
  framework, with explicit governance, environment-isolation, provenance, MCP,
  adapter, validation, and distribution boundaries.
- Excluded: code-policy changes, research-data access, secrets, package/model
  operations, network, and release actions.

### Results

- Added `docs/research-operations-specification.md` as one standalone
  specification of the implemented framework. It covers authority, Core records,
  evidence, approval, amendments, agent governance, environment isolation,
  path/secret boundaries, adapters, MCP, plugin, validation, CI, and release.
- The document contains no secret, local absolute path, or reference-project
  research result and introduces no runtime behavior change.

## 2026-08-04 — MCP A/B benchmark preparation plan

### Ownership

- Requested and approved by: User.
- Planned and implemented by: Codex.
- Benchmark execution and final protocol approval remain separate human-gated
  steps.

### Preparation scope

- Preregister the A/B design, endpoints, fairness constraints, isolation,
  leakage controls, randomization, replication, stopping rules, and analysis.
- Add provider-neutral task/run contracts and account separately for model input,
  output, cached-input, reasoning, total/billable tokens, model calls, MCP calls,
  generic tool calls, tool payload bytes, latency, and estimated cost.
- Add synthetic holdout fixtures and deterministic bundle/scoring tests only.

### Exclusions

- No live API/model/MCP benchmark run, API token handling, package/model
  installation, network, reference-project data, historical result/log change,
  semantic model load, benchmark claim, or public release during preparation.

## 2026-08-04 — Codex-only GitHub preview publication plan

### Ownership

- Requested and approved by: User.
- Planned and executed by: Codex.
- Publication target: `mp-juns/universal-research-mcp` through an isolated
  `agent/codex-only-preview` branch and draft pull request.

### Planned scope

- State that Codex is the only supported host integration in this preview.
- Keep model selection, native agent sessions, tool execution, and approvals
  host-owned by Codex.
- Remove the provider-backed runtime MCP and console launcher from the default
  plugin and distribution surface.
- Hide unfinished local/OpenAI/Anthropic provider and agent-runtime CLI routes
  behind an explicitly internal development gate.
- Remove provider-status advertising from the default MCP surface.
- Preserve internal prototypes for later work without claiming compatibility or
  support for Ollama, OpenAI API, Anthropic API, Moonshot/Kimi, Claude Code,
  OpenCode, or OpenClaw.
- Keep host visualization disabled by default and available only after explicit
  user opt-in in a supported host flow.

### Planned files and verification

- Public contract: `README.md`, `mcp/README.md`, `pyproject.toml`, Codex plugin
  manifest/configuration/Skills, host and architecture documentation.
- Runtime gates: unified CLI, default MCP tool registration, distribution
  validator, CI/publish smoke checks, and their contract tests.
- Verification: diff safety scan, complete local pytest suite, package build,
  wheel-content validator, installed-wheel Codex-only smoke, both plugin Skill
  validators, and the Codex plugin manifest validator.

### Exclusions

- No model or API call, benchmark execution, API credential handling, PyPI
  release, GitHub Release, reference-project access, canonical research-data
  write, marketplace rewrite, or support claim for a non-Codex host.

### Local results before publication

- The default Codex plugin now registers only the read-only/governance MCP.
- The distribution no longer publishes a provider-backed runtime entry point.
- Default CLI help exposes only `serve`, `init`, `index`, `build-index`,
  `doctor`, and `validate`; unfinished provider/agent routes are retained only
  behind an internal development gate and have no support contract.
- `doctor` reports `supported=[codex]`, Codex-host-owned model execution,
  external provider execution as unsupported, and host visualization off.
- Focused public-contract and CLI checks passed: 22 tests.
- Complete local suite passed: 236 passed, 2 skipped.
- Both plugin Skills and the Codex plugin manifest validated successfully; the
  cachebuster was refreshed once after the manifest change.
- The installed-wheel smoke initialized an isolated project and verified the
  Codex-only entry-point set and CLI surface.
- `python -m build` was unavailable because the environment lacks the PyPA
  `build` package and the existing build-artifact directory resolves as a
  namespace package. No dependency was installed. The equivalent wheel was
  created offline with the installed pip/setuptools backend and passed the
  repository distribution validator. GitHub CI remains responsible for the
  clean PyPA build and source-distribution check.

### GitHub publication result

- Committed the reviewed implementation as `6f8ec42` on
  `agent/codex-only-preview` and pushed it to the public origin.
- Opened draft pull request #1 against `main`:
  `https://github.com/mp-juns/universal-research-mcp/pull/1`.
- Both GitHub Actions `core-contracts` runs triggered by the branch push and
  pull request completed successfully on clean hosted runners.
- The pull request remains draft; no merge, GitHub Release, or PyPI publication
  was performed.

## 2026-08-04 — main merge and PyPI 0.3.0 release plan

### Ownership and authorization

- Requested and approved by: User.
- Planned and executed by: Codex.
- Targets: pull request #1 → `main`, GitHub Release `v0.3.0`, and PyPI project
  `universal-research-mcp` version 0.3.0 through Trusted Publishing.

### Verified preconditions

- GitHub authentication is active for repository owner `mp-juns`.
- PR #1 head `77aa00e381c4a1bbde7726d311968be4d15ce93f` is mergeable and clean;
  both push and pull-request `core-contracts` checks passed.
- The only existing GitHub Release is `v0.1.0`; `v0.3.0` is unused.
- The PyPI JSON endpoint for version 0.3.0 returned HTTP 404.
- `.github/workflows/publish.yml` builds, validates, smoke-tests, and publishes
  with GitHub OIDC only when a GitHub Release is published.

### Authorized sequence and stop conditions

- Record and push this plan, wait for the exact final PR head CI, mark the PR
  ready, and squash-merge it into `main` with an expected-head guard.
- Wait for `main` CI before creating the release. Create `v0.3.0` as a draft,
  inspect its target and metadata, then publish it once.
- Stop without retrying publication if CI, artifact validation, Trusted
  Publishing, or the PyPI version check fails unexpectedly.
- No model/API inference, benchmark execution, credential reading, reference
  project access, or canonical research-data write is authorized.

### Release outcome

- Final PR head `23e5862fd526ee21b6f70e5fa61da31760a7e954` passed both
  push and pull-request CI checks.
- PR #1 was marked ready and squash-merged with the expected-head guard. The
  resulting `main` commit is `20edbf462609f91a59abe0f4cccacf54f91ed9b1`.
- The merged `main` CI run `30861570759` passed the full tests, distribution
  build/inspection, and installed-wheel initialization smoke.
- Draft release `v0.3.0` was verified to target the exact merge commit and then
  published at `https://github.com/mp-juns/universal-research-mcp/releases/tag/v0.3.0`.
- Trusted Publishing run `30861655499` passed tests, artifact inspection,
  installed-wheel smoke, OIDC publication, and digital attestation upload.
- PyPI version endpoint for 0.3.0 returned HTTP 200 after publication. A fresh
  isolated environment downloaded and installed
  `universal-research-mcp==0.3.0` from the public PyPI index; package metadata,
  version output, Codex-only CLI commands, and absence of the provider runtime
  console entry point were verified.
- The only non-blocking workflow annotation was the hosted-runner Node.js 20
  deprecation notice for upstream checkout/setup actions.
