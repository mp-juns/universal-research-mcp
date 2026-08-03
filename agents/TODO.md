# TODO

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
