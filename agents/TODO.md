# TODO

## Completed task — Real multi-agent runtime, excluding unavailable host internals

### Objective

Turn the existing eleven-role governance and dispatch foundation into an actual
multi-agent runtime: each activated role receives a dedicated prompt and
isolated session, the scope governor runs first, approved workers execute as
separate provider calls with bounded parallelism, and every dispatch, decision,
failure, and session transition is recorded without conflating host GUI agents
with plugin-owned agents.

### Approved scope

- Requested by: User (`그런타임을 구축해야지 불가능한거 빼고`).
- Runtime: eleven role-specific prompt packs; isolated per-agent session state;
  governor-first execution; deterministic receipt and operation gates; bounded
  parallel workers; no hidden retry; append-only runtime events and per-agent
  artifacts; concise run status and inspection.
- Providers: existing explicit-budget OpenAI and Anthropic generation; add a
  loopback-only OpenAI-compatible local generation adapter suitable for a
  user-managed Ollama, llama.cpp, vLLM, or compatible endpoint. Credentials may
  be referenced through environment/keyring only and never accepted as values.
- Surfaces: management CLI plus explicitly approved MCP runtime tools; preserve
  the host-owned Codex dispatch-manifest path for environments whose private
  scheduler is unavailable to plugins.
- Packaging: versioned schemas/support bundle, plugin Skill/reference updates,
  focused and full local tests, clean-wheel smoke, plugin cachebuster, local
  package/plugin activation after validation.

### Evidence-based effort and limits

- Necessity: required; role manifests alone do not satisfy the requested
  independently executing multi-agent system.
- Difficulty: high.
- Estimated work: read 40–60 files, modify 25–40 files, add or update 35–60
  tests, zero model runs, zero benchmark runs, zero paid API calls.
- Expected engineering range: roughly 2–4 hours for implementation and local
  fixtures, with higher uncertainty only if packaging or MCP transport contracts
  expose an unexpected incompatibility.
- Excluded as unavailable or unauthorized: direct access to the Codex/ChatGPT
  private scheduler, subscription/entitlement bypass, forced native GUI task
  creation, actual provider calls, credential entry, model download/start,
  benchmark, visualization, GitHub/PyPI publication, and writes to any reference
  research project.

### Success criteria

- [x] Eleven complete role prompts are loaded and hash-bound to each role/session
- [x] A real runtime creates isolated agent sessions and executes one provider call per activated role
- [x] Scope governor executes first and all worker dispatches require its exact receipt
- [x] Independent workers run concurrently only within declared worker/cost limits
- [x] Local loopback and remote provider modes fail closed without explicit configuration and approval
- [x] Session events, decisions, failures, and usage are append-only and separately inspectable
- [x] CLI and MCP expose preflight, run, status, and run inspection without accepting secrets
- [x] Tests prove isolation, ordering, concurrency, stop policy, budgets, prompt binding, and persistence
- [x] Wheel, plugin, Skills, fresh install, and installed runtime smoke all validate

## Active task — Installed 0.2.0 bounded end-to-end test

### Approved scope

- Requested by: User (`한번 테스트 작업해봐`).
- Test task: create an isolated temporary research project containing one
  synthetic, non-sensitive research note and one schema-valid canonical event;
  run the installed 0.2.0 governance preflight, automatic derived-index refresh,
  DB-first candidate search, exact source/hash fetch, and one deliberate
  out-of-scope operation evaluation.
- Parallel review: use host subagents only for independent read-only inspection
  of the memory, index, and governance test contracts. They may not modify or
  execute the test project.
- Success: the approved local task passes; lexical/semantic health remains
  current; the known passage is retrieved and source-verified; the deliberate
  violation is blocked; no false semantic-search claim is made.
- Excluded: external API/model call, credential access, network, dependency or
  model download, benchmark, visualization, source-repository research-event
  append, background worker, GitHub/PyPI publication, and persistent user data.

### Completed

- [x] Validate the exact installed CLI/MCP test contract
- [x] Run the temporary governed memory/index test
- [x] Independently review observed results and record the verdict

### Findings requiring a later product decision

- [ ] Decide whether completed Core records with future `occurred_at` or
  `recorded_at` values should emit a temporal-integrity audit finding.
- [ ] Repeat the MCP transport and host-Skill test in a newly started Codex task
  so the host loads the installed 0.2.0 plugin catalog instead of the prior
  task's stale capability snapshot.

## Active task — Activate the verified 0.2.0 package and Codex plugin

### Approved scope

- Requested by: User (`ㄱㄱ`) after the 0.2.0 build handoff.
- Actions: rebuild and validate the local wheel; replace the user-environment
  `universal-research-mcp` installation; reinstall
  `universal-research-memory@universal-research-local`; verify installed
  versions, plugin cache, MCP registration, and a temporary fresh-project init.
- Excluded: PyPI publish, GitHub push, API/model calls, credentials, benchmark,
  canonical research writes, visualization, and marketplace-file hand edits.

### Completed

- [x] Reconfirm installed package, plugin, and marketplace source
- [x] Install the verified local 0.2.0 wheel
- [x] Reinstall the local Codex plugin through the CLI
- [x] Verify activation and record the handoff

## Active task — Unified plugin, automatic DB, providers, and scope enforcement

### Objective

Merge the local memory/governance work into one installable MCP/plugin runtime,
automatically create a safe derived lexical database for fresh projects, add
capability-based local/OpenAI/Anthropic provider routing, introduce the
user-approved eleventh `scope_and_cost_governor`, and make failure stop/retention
policy configurable without weakening the minimum audit trail.

### Evidence-based effort assessment

- Difficulty: high.
- Repository audit found eleven integration gaps: package exclusion of builders,
  no empty bootstrap, unresolved profile paths, incorrect source-root assumption,
  non-atomic replacement, watcher bootstrap/failure gaps, no provider contract,
  local-encoder coupling, missing optional dependencies, split MCP processes,
  and divergent governance contracts.
- Work streams: governance migration, runtime/index packaging, provider/security,
  failure policy, unified MCP/CLI, and contract/security fixtures.
- Estimate basis: affected components and required fixture families in this
  repository, not an unsupported wall-clock promise. Production release work
  remains separate from this implementation pass.

### Approved scope

- Requested by: User, including explicit approval to change the fixed roster
  from ten to eleven roles and add configurable failure retention.
- Target files: `governance/`, `universal_research_mcp/runtime/`,
  `universal_research_mcp/indexing/`, `universal_research_mcp/providers/`,
  unified server/CLI, package/plugin config, schemas, focused tests, docs, and
  project work records.
- Commands: local source inspection; focused unit/contract/security tests;
  package/plugin/Skill validation and cachebuster only.
- Success: eleven-role `agent-governance/2.0` fail-closed registry with the
  final `scope_and_cost_governor` name; always-on preflight scope review;
  deterministic plan/work estimate evidence; immediate stop on failure;
  configurable `full|metadata_only|ask` retention with minimum stopped-work
  trace; automatic atomic lexical DB creation; one MCP surface; provider
  capability/status without secret disclosure; no-key lexical-only operation;
  host visualization capability disabled until explicit user opt-in.
- Excluded: repository download; source-project access; real external API call;
  real key entry; package install; model download; actual experiment/benchmark;
  background watcher; canonical research result creation; remote push/release;
  marketplace change; raw API key in chat/config/log/ledger/MCP result.

### Planned verification commands

- `python3 -m unittest` for new roster, planning/scope, failure-policy,
  index-bootstrap/atomicity, provider-security, unified-server, and existing
  governance/memory fixtures.
- `python3 -m universal_research_mcp.cli doctor <temporary-project>` and index
  status against temporary fixtures only.
- Skill/plugin validation and local cachebuster update.

### Remaining

- [x] Upgrade governance to eleven roles and add hash-bound scope/cost enforcement
- [x] Add configurable fail-stop and failure-retention policy
- [x] Add opt-in-only host visualization capability policy
- [x] Package atomic lexical/semantic bootstrap, health, and safe runtime paths
- [x] Add provider capability, credential-reference, and remote opt-in contracts
- [x] Add bounded parallel harness with mandatory governor receipt and explicit costs
- [x] Unify memory/governance/status/provider tools in one MCP and CLI
- [x] Add focused fixtures, full CI/publish gates, wheel checks, and docs

### Deferred product decisions

- [ ] Connect a governed query-time encoder/ranker before exposing semantic or hybrid MCP search
- [ ] Add a concrete local generation runtime adapter; the current local provider contract is injectable
- [ ] Complete non-interactive CLI receipts for the `ask` failure-detail choice and secure keyring onboarding
- [ ] Migrate generic top-level Python packages into the `universal_research_mcp` namespace
- [ ] Split English `README.md` and Korean `README.ko.md`
- [ ] Add a policy-defined future-timestamp audit rule for completed canonical records
- [x] Install the 0.2.0 wheel and refreshed plugin into the active Codex environment after explicit approval

## Active task — Codex dispatch-manifest handoff

### Objective

Complete the local host handoff by exporting validated Codex dispatch manifests
and accepting structured decisions through a no-write diagnostic interface. The
host, not the plugin, remains the executor of any parallel agent work.

### Approved scope

- Requested by: User ("ㄱㄱ")
- Target files: Codex adapter, `urgov` CLI, read-only governance MCP, focused
  adapter fixtures, plugin governance Skill, host documentation, and work logs.
- Commands: focused local tests, CLI export validation, plugin/Skill validation,
  and cachebuster update only.
- Success: one packet or an isolated critical-review batch can be exported as a
  canonical JSON manifest; the manifest exposes no private scheduler/model
  capability; returned structured output can be captured as accepted or invalid.
- Excluded: native agent spawning, any model/API call, canonical ledger write,
  real project file write, network, daemon, package install, marketplace, and
  correction/experiment/benchmark execution.

### Remaining

- [x] Add manifest serialization and CLI/MCP handoff surfaces
- [x] Add round-trip fixture coverage
- [x] Run only the approved local checks

## Active task — Codex host-adapter foundation

### Objective

Add the safe Codex-side bridge for URAG: produce role-scoped dispatch requests,
preserve critical-reviewer isolation, and capture only schema-valid structured
decisions. The bridge never assumes a plugin can call Codex's private agent
runtime; the host remains responsible for dispatch under its own entitlement.

### Approved scope

- Requested by: User ("ㄱㄱ")
- Target files: `integrations/codex/adapter.py`, role instruction renderers,
  Codex adapter tests, governance Skill, host-integration documentation, and
  work records.
- Commands: source inspection; focused Codex-adapter/governance tests; existing
  local plugin/Skill validation and cachebuster update. No native subagent,
  server daemon, model endpoint, network, ledger write, correction, benchmark,
  or repository fixture execution.
- Success: a valid task packet maps to a host-independent Codex dispatch
  request; the request cannot add tools/actions; each critical reviewer gets an
  isolated request with the same evidence snapshot; invalid decision output is
  retained as an invalid artifact and never promoted to a result.
- Excluded: actual Codex-agent spawning, any model configuration or billing
  change, remote/hosted inference, package installation, real write execution,
  external integrations, and marketplace/release work.

### Remaining

- [x] Add role-scoped Codex dispatch and decision-capture adapter
- [x] Add critical-review isolation fixture coverage
- [x] Update host-facing Skill and documentation
- [x] Run only the approved local checks

## Active task — URAG v1 Phase 0–1 optimization

### Objective

Adapt the user-supplied URAG v1 specification to the installed local Codex
environment. Build deterministic governance contracts and read-only local
interfaces while preserving host-provided inference: this plugin validates and
orchestrates policy but never proxies or pays for an LLM.

### Approved scope

- Requested by: User ("이제 이걸 이환경 그리고 너가 알고 있는대로 최적화해서 하자")
- Target files: `governance/` package and role manifests; governance schemas;
  `universal_research_mcp/governance_server.py`; CLI/package/plugin config;
  focused fixtures; architecture, security, host-integration, and v1 plan docs;
  project work records.
- Commands: local source inspection; focused `unittest` for governance and
  existing contract/framework modules; CLI registry/packet/decision checks;
  local Skill/plugin validation and plugin cachebuster update. No server daemon
  is started.
- Success: exact manifest-backed ten-role registry; canonical hashes for role,
  task, and decision artifacts; deterministic mode/state/gate evaluation;
  fail-closed scope/approval/evidence checks; read-only governance MCP and CLI
  surfaces; Codex plugin configuration that exposes both local MCP servers.
- Excluded: external model invocation; API keys; model downloads; semantic or
  lexical index rebuild; canonical ledger writes; project-data changes; actual
  worker dispatch; benchmark/experiment execution; network; package install;
  daemon/background process; Claude Code or ChatGPT runtime deployment;
  marketplace registration and remote publication.

### Planned files

- `governance/{registry,validation,workflow,escalation,hashing,cli,errors}.py`
  and `governance/roles/*/role.yaml`
- `governance/schemas/*.json`, `tests/test_urag_governance.py`
- `universal_research_mcp/governance_server.py`, `pyproject.toml`,
  `plugin/universal-research-memory/.mcp.json`, plugin cachebuster manifest
- `docs/{urag-v1-design,security,host-integration,workflow-modes,role-authority}.md`
- `agents/TODO.md`, `agents/WORK_LOG.md`

### Remaining

- [x] Add registry, role manifests, hashing, and validation contracts
- [x] Add deterministic workflow and publication gates
- [x] Add read-only governance CLI/MCP host surfaces
- [x] Add focused adversarial/contract fixtures and documentation
- [x] Run only the approved local checks

## Active task — governed multi-agent research foundation

### Objective

Extend the independent Universal Research plugin with a deterministic governance
layer for the user-defined ten-agent roster, central-manager disclosure policy,
and derived-index refresh eligibility.  This phase establishes contracts and
policy checks only; it does not run models, experiments, remote services, or
background workers.

### Approved scope

- Requested by: User ("그럼 일단 구축하십쇼")
- Target files: `core/governance.py`, `core/index_refresh.py`, versioned
  agent-governance schemas, fixture tests, the plugin governance Skill, and
  architecture/operations documentation.
- Commands: source inspection; `python3 -m unittest discover -s tests -p
  'test_governance.py'`; existing contract/framework test modules; local plugin
  and Skill validation only.
- Success: exactly ten registered roles, three fixed modes, authority checks,
  task/decision record validation, critical-review claim gates, summary-only
  chat disclosure checks, and a derived-index refresh eligibility contract.
- Excluded: reference-project access; canonical event append; project index
  rebuild; embedding/model loading; package installation; network; daemon or
  background work; remote execution; real benchmark/experiment execution;
  marketplace or publication changes.

### Planned files

- `core/governance.py`, `core/index_refresh.py`, `core/__init__.py`
- `schemas/research-agent-task.schema.json`,
  `schemas/research-agent-decision.schema.json`,
  `schemas/index-health.schema.json`
- `tests/test_governance.py`, `tests/test_contract_files.py`
- `docs/multi-agent-governance.md`, `docs/architecture.md`, `README.md`
- `plugin/universal-research-memory/skills/research-governance/SKILL.md`
- `agents/TODO.md`, `agents/WORK_LOG.md`

### Remaining

- [x] Record planned foundation work
- [x] Add governed-role and central-manager contracts
- [x] Add derived-index refresh eligibility and failure-state contract
- [x] Add fixture validation and documentation
- [x] Run only the approved local checks

## Active task

- [x] `universal_research_mcp` 독립 프로젝트 bootstrap 및 1차 MCP/plugin 이주

### Objective

기존 연구 프로젝트의 MCP 구현과 연구-memory DB 구조를 참고해, 원본 프로젝트와 runtime 및 작업 기록을 공유하지 않는 범용 연구 MCP와 Codex 플러그인을 만든다.

### Approved scope

- 대상: `<workspace-root>`
- 참고 원본: `<reference-project-root>`
- 허용: 원본의 MCP Python, 연구-memory index builder/query 코드, agent 문서 구조, DB schema read-only inspection 및 복사
- 제외: 원본 수정, 기존 SQLite/embedding DB 복사, 실험, benchmark, network, background watcher, remote 작업

### Planned files

- `README.md`, `AGENTS.md`
- `agents/AGENT_RULES.md`, `agents/TODO.md`, `agents/WORK_LOG.md`, `agents/README.md`
- `mcp/project_search/`
- `scripts/`
- `db/schema/`, `config/profile.yaml`
- `plugin/universal-research-memory/`

### Success criteria

- 새 폴더가 독립 운영 문서와 plugin manifest를 가진다.
- 원본 MCP 관련 코드가 새 `mcp/`와 `scripts/` 아래에 복사된다.
- 기존 SQLite 파일은 새 폴더에 복사되지 않는다.
- DB 구조가 schema reference로 보존된다.
- 새 코드의 기본 경로가 원본 DB를 write 대상으로 지정하지 않는다.

### Completed in this pass

- [x] 독립 폴더와 plugin scaffold 생성
- [x] README에 목적·경계·이주 범위 정의
- [x] 독립 `AGENTS.md`, `AGENT_RULES.md`, `TODO.md`, `WORK_LOG.md` 작성
- [x] MCP/API 및 research-memory index 관련 소스 복사
- [x] 참고 DB schema를 새 SQL contract로 작성
- [x] MCP 기본 경로를 새 `data/index/`로 변경
- [x] plugin manifest validation 통과

### Remaining

- [ ] 복사된 scripts의 project-specific naming과 default path를 generic adapter로 분리
- [ ] event append/write MCP contract 설계
- [ ] plugin 설치 경로에 의존하지 않는 MCP launch configuration 정리
- [ ] 별도 fixture 기반 MCP contract validation 계획 수립

## Active task — generic adapter separation

- [ ] MCP/API와 설정 경로를 generic runtime adapter로 분리

### Objective

복사된 MCP가 특정 연구 폴더의 이름이나 DB 경로를 직접 가정하지 않도록 runtime root, 자체 index, read-only reference root를 한 설정 계층에서 관리한다.

### Approved scope

- 수정 범위: `mcp/project_search/`, `config/profile.yaml`, `README.md`, 독립 agent 기록
- 허용: 새 adapter 모듈 생성, MCP/API의 경로 참조 수정, generic 설정 문서 추가
- 제외: 기존 연구 폴더 수정, 기존 DB 사용·복사, event append, index rebuild, server 실행, test/benchmark, network/background/remote 작업

### Success criteria

- runtime 설정이 새 프로젝트의 자체 `data/index/`를 기본값으로 사용한다.
- 원본 프로젝트 경로는 명시적인 read-only reference 설정으로만 존재한다.
- MCP/API가 adapter를 통해 경로를 얻고, 프로젝트 이름에 의존하는 기본 경로를 갖지 않는다.
- 새 capability나 실행 작업 없이 문서와 source-level 변경만 완료한다.

## Completed framework implementation

### Objective

Bootstrap search memory를 범용 연구 운영 코어로 확장한다. 코어는 승인,
append-only amendment, source-grounded claim, contribution, reproducibility,
read-only audit를 정의하고 project-specific 기능은 pack/profile/adapter로
격리한다.

### Approved scope

- 대상: 이 프로젝트의 `core/`, `schemas/`, `packs/`, `adapters/`, `mcp/`,
  `scripts/`, plugin Skill, fixture/tests 및 운영 문서
- 허용: 독립 core 구현, derived-index validation 연결, local read-only MCP,
  fixture 기반 검증
- 제외: 원본 프로젝트, historical result/DB/log, index 생성, 모델 로드,
  network, package 설치, marketplace 등록

### Completed

- [x] Core/Park/Project Profile versioned contracts
- [x] Legacy event compatibility validation and source-grounded core records
- [x] Fail-closed amendment resolved view and approval-gated append helper
- [x] Read-only audit and Markdown projection adapters
- [x] Lexical/semantic derived-index validation hooks
- [x] Local read-only MCP audit surface and evidence-first plugin Skill
- [x] Fixture, compatibility, Skill, and plugin validation

## Active task — Core retrieval and approval P0 hardening

### Objective

Close the reviewed gap between `core/1.0` canonical records and the existing
derived retrieval path, and make the append boundary verify real human approval
scope rather than an approval-shaped string.

### Approved scope

- Requested by: User
- Target files: `core/indexing.py`, `core/proposals.py`, lexical/semantic index
  builders, read-only MCP, fixture tests, and architecture/contract docs.
- Commands: targeted `unittest` and `pytest` validation only.
- Success: a Core claim preserves relation and source provenance through both
  index projections; MCP fetch compares indexed and current hashes; fabricated
  approval references are rejected.
- Excluded: reference project access, historical-ledger changes, package
  installation, network, daemon startup, and any new write MCP capability.

### Completed

- [x] Core-to-index compatibility projection for lexical and semantic builders
- [x] Real, human-issued, approved, scoped approval verification at append
  boundary
- [x] Indexed/current source-hash integrity status in evidence fetch
- [x] Core-only candidate → fetch → audit E2E and negative approval fixtures

## Active task — public usability and contract parity hardening

### Objective

Turn the reviewed bootstrap into an installable local package and a
path-independent plugin launcher, close the declared-schema/manual-validator
contract gap with parity coverage, and document a short reproducible start path.

### Approved scope

- Requested by: User (continue after P0)
- Target files: package metadata/entry point, read-only MCP launcher, plugin
  manifest/config, validator and tests, README/docs, LICENSE, and agent records.
- Commands: local unit tests, plugin validation/cachebuster/reinstall, Git commit
  and push to the already authorized public repository.
- Success: `uv run universal-research-mcp --help` exposes a stable entry point;
  plugin config has no repository-relative implementation path; core parity
  cases detect contract drift; public license and 5-minute Quick Start exist.
- Excluded: reference-project writes/reads, research data or historical-log
  changes, remote research execution, model download, benchmark, and new MCP
  write tools.

### Completed

- [x] `pyproject.toml` package metadata and stable MCP entry point
- [x] Plugin launcher no longer reaches into repository-relative `mcp/` or data
  paths
- [x] MIT license and Quick Start
- [x] Schema/manual validator parity fixture and expanded structural validation
- [x] Explicit symlink and sensitive-path safety fixture
- [x] Plugin cachebuster, validation, and local marketplace reinstall

## Completed task — retrieval failure-mode fixtures

### Objective and scope

- Requested by: User, continuing the public-project hardening work.
- Target: read-only evidence fetch and lexical index builder fixtures only.
- Success: fetched content and current hash come from one byte snapshot;
  malformed canonical JSONL fails closed; a large fixture ledger indexes without
  canonical mutation.
- Excluded: project ledger/index writes, reference project access, packages,
  network, daemon, benchmark, and write MCP tools.

### Completed

- [x] One-read evidence snapshot before rendering and SHA-256 calculation
- [x] Malformed JSONL failure fixture
- [x] 1,000-record derived-index fixture with canonical-input preservation

## Active task — local package installation verification

### Objective and scope

- Requested by: User (explicit continuation after the missing-`uv` report).
- Command: `python3 -m pip install --user --no-deps .`, followed by the installed
  console entry point's `--help` and `--version` checks.
- Success: the existing plugin command `universal-research-mcp` resolves from
  the local user environment without a network dependency.
- Excluded: dependency download, reference project, research data/historical
  logs, server startup, model, benchmark, and remote work.

### Completed

- [x] User-approved local package installation
- [x] Installed `universal-research-mcp --version` and `--help` verification

## Active task — public CI baseline

### Objective and scope

- Requested by: User, continuing the public-project hardening work.
- Target: a GitHub Actions workflow that installs the package with its test
  extras and runs the package/core/read-only-MCP/lexical fixture checks.
- Success: each public push and pull request has a repeatable baseline without
  model loading, semantic-index dependencies, remote research execution, or
  access to any reference project.
- Excluded: PyPI publishing, secret configuration, deployment, benchmark,
  semantic encoder tests, and repository data changes.

### Completed

- [x] Python 3.11 GitHub Actions workflow for push and pull-request checks
- [x] Package entry-point, core, read-only MCP, and lexical fixture baseline

## Completed task — optional semantic runtime contract

### Objective and scope

- Requested by: User, continuing public-project completion work.
- Target: package metadata and README only.
- Success: semantic encoder dependencies are opt-in, while the default MCP
  remains lexical, read-only, and model-load-free.
- Excluded: dependency installation, model download, semantic index build,
  benchmark, and any reference-project or research-data operation.

### Completed

- [x] Declared a separate `semantic` package extra
- [x] Documented its approval-gated, non-automatic model boundary

## Active task — PyPI Trusted Publishing release

### Objective and scope

- Requested by: User, after confirming PyPI Trusted Publishing setup is complete.
- Target: a release-only GitHub Actions workflow and the initial `v0.1.0`
  GitHub Release for the existing `0.1.0` package metadata.
- Commands: local package-build metadata validation, Git commit/push, then
  GitHub Release creation and read-only workflow status inspection.
- Success: release workflow builds a wheel/sdist, checks artifacts, and publishes
  with GitHub OIDC (`id-token: write`) rather than a repository secret.
- Excluded: API-token handling, secret files, reference project/data access,
  model/semantic execution, benchmark, and any MCP write capability.

### Completed

- [x] Release-published-only OIDC PyPI workflow
- [x] Initial public `v0.1.0` GitHub Release
- [x] Build, artifact check, PyPI publish, and digital attestation workflow

## Active task — consolidated operating specification

### Objective and scope

- Requested by: User.
- Target: one Markdown specification under `docs/` that describes the complete
  research operations framework, including governance, isolation, data, adapter,
  MCP, validation, and distribution boundaries.
- Success: the document stands alone without exposing a secret, local absolute
  path, or reference-project research result.
- Excluded: implementation or policy changes, research-data changes, package
  installation, network, and release actions.

### Completed

- [x] Standalone framework specification covering governance through release

## Active task — MCP A/B benchmark preparation

### Objective and scope

- Requested and approved by: User.
- Prepare only: preregistered protocol, isolated environment contract,
  condition/task/run schemas, token-and-call accounting, deterministic scoring,
  synthetic holdout fixtures, and local validation tests.
- Primary comparison: identical model/prompt/source snapshot with ordinary
  read-only filesystem retrieval versus Universal Research MCP retrieval.
- Planned files: `benchmarks/`, benchmark schemas/config/fixtures, scoring and
  validation scripts, tests, and operating records.
- Planned commands: read-only source inspection and local fixture/unit tests.
- Success: benchmark bundle validates; paired scoring includes prompt, output,
  cached, reasoning, and total tokens plus model/MCP/tool call counts; execution
  cannot begin without a separate approved protocol/run configuration.
- Excluded: live model/API calls, secrets, package/model download, benchmark
  execution, reference-project data, existing research results/logs, semantic
  model loading, remote work, and public release/push until preparation passes.

## Active task — Codex-only 0.3.0 GitHub preview

### Objective and scope

- Requested and approved by: User.
- Publish the accumulated governance, evidence, indexing, and Codex adapter work
  without claiming support for another host or model provider.
- Supported public integration: Codex plugin with host-owned model selection,
  agent sessions, tool execution, and approvals.
- Excluded: Ollama/OpenAI/Anthropic/Moonshot execution, Claude Code, OpenCode,
  OpenClaw, PyPI release, model/API/benchmark calls, credentials, and research
  data writes.

### Completed locally

- [x] Codex-only support banner and roadmap boundaries
- [x] Provider-backed runtime removed from plugin MCP and console entry points
- [x] Provider/agent prototype commands hidden from the default CLI
- [x] Default MCP provider-status advertisement removed
- [x] Codex plugin and both Skills validated
- [x] Complete test suite passed: 236 passed, 2 skipped
- [x] Wheel content and isolated installed-wheel smoke passed

### Remaining publication gate

- [x] Commit the reviewed worktree on `agent/codex-only-preview`
- [x] Push the branch and open draft pull request #1 against `main`
- [x] Confirm both push and pull-request GitHub CI runs passed

## Active task — merge 0.3.0 and publish to PyPI

### Objective and authority

- Requested and approved by: User.
- Merge draft pull request #1 into `main`, publish GitHub Release `v0.3.0`,
  and let the existing OIDC Trusted Publishing workflow upload version 0.3.0
  to PyPI.
- Use the exact reviewed PR head and stop if the head moves, CI fails, the
  release/tag exists, or PyPI already contains version 0.3.0.

### Preflight

- [x] PR #1 is open, draft, mergeable, and clean against `main`
- [x] Both final-head GitHub CI checks passed
- [x] Package and plugin versions are 0.3.0
- [x] GitHub release/tag `v0.3.0` does not exist
- [x] PyPI version endpoint for 0.3.0 returned 404
- [x] Release workflow publishes only on a published GitHub Release with OIDC

### Release sequence

- [ ] Push this release authorization record and require CI success
- [ ] Mark PR #1 ready and squash-merge the exact final head into `main`
- [ ] Confirm the merged `main` CI result
- [ ] Create and inspect draft GitHub Release `v0.3.0`
- [ ] Publish the release and confirm the Trusted Publishing workflow succeeds
- [ ] Verify PyPI metadata and an isolated installation of version 0.3.0
