# Universal Research Memory MCP

이 폴더는 특정 모델이나 특정 연구 프로젝트에 종속되지 않는 연구 운영용 MCP와 Codex 플러그인을 만드는 독립 프로젝트다.

## 목적

- 연구 계획, 승인, 실행, 실패, 결정, 결과, 출처를 추적하는 공통 운영 규칙을 제공한다.
- 기존 연구 프로젝트의 BM25/FTS5 및 dense embedding DB 구조를 참고해 재현 가능한 연구 기억 저장소를 설계한다.
- `research_search`, `research_latest`, `research_fetch` 같은 source-grounded MCP 도구를 범용화한다.
- Codex 플러그인으로 설치할 수 있는 독립적인 연구 작업 환경을 제공한다.

## 현재 연구 폴더와의 경계

원본 프로젝트는 다음 경로에 있으며 설계 참고용 read-only 입력이다.

```text
<reference-project-root>
```

이 프로젝트는 원본 프로젝트에 다음을 하지 않는다.

- 파일 생성·수정·삭제·이동
- 기존 연구 이벤트 JSONL 또는 `sqlite3` DB 사용·변경
- 기존 연구 세션의 TODO, WORK_LOG, 결과, benchmark 기록에 append
- Qwen, UNO Q, ScamGuardian 또는 특정 연구 결과를 범용 규칙으로 가정

원본의 embedding DB와 MCP 소스는 schema와 동작을 이해하기 위한 참고 원본이다. 새 MCP의 runtime 저장소와 event ledger는 이 폴더 안에서 독립적으로 생성한다. 원본 DB를 새 폴더로 복사하지 않는다.

## 초기 이주 범위

다음 항목을 원본에서 보존적으로 복사하고 범용화한다.

- `tools/project_search/`의 MCP proxy, API server, query expansion, export 코드
- 연구 ledger 조회·lexical index·semantic index·watcher에 필요한 `scripts/` 모듈
- MCP 전용 requirements와 환경설정 예시
- `AGENT_RULES.md`, `TODO.md`, `WORK_LOG.md`, agent 운영 README의 구조와 attribution 규칙
- 기존 `research.sqlite`와 `semantic.sqlite`의 테이블·인덱스 구조를 정리한 schema reference

다음 항목은 이주하지 않는다.

- 기존 `research-events/index/*.sqlite*`
- 기존 연구 결과, historical logs, 모델 파일, virtual environment
- Qwen classifier 전용 training·benchmark·deployment 코드
- API key와 secret

## 설계 구조

```text
universal_research_mcp/
  mcp/                         # 독립 MCP/API 구현
  plugin/                      # Codex plugin
  scripts/                     # ledger/index 유지보수 도구
  db/schema/                   # 참고 DB에서 추출한 독립 schema
  data/events/                 # 새 프로젝트의 canonical append-only events
  data/index/                  # 새 프로젝트의 파생 lexical/dense index
  config/                      # 원본 read-only reference와 자체 저장소 설정
  agents/                      # 이 프로젝트 전용 운영 규칙과 작업 기록
  core/                        # dependency-free core validation and vocabulary
  schemas/                     # versioned core, pack, and profile contracts
  packs/                       # study-type and domain constraints
```

## 데이터 권위

1. `data/events/`의 JSONL이 새 프로젝트의 canonical event ledger다.
2. `data/index/`의 SQLite 및 embedding index는 재생성 가능한 derived view다.
3. 원문 source와 artifact는 MCP 검색 결과의 후보를 검증하는 근거다.
4. embedding similarity 결과만으로 사실이나 인과를 단정하지 않는다.

`TODO.md`, `WORK_LOG.md`, and `agents/sessions/` are human-readable views of
plan, session, decision, and contribution records. They are not a replacement
for the canonical ledger.

## 범용 운영 코어

- `schemas/core-record.schema.json` defines immutable core records and typed
  relations.
- `schemas/pack-manifest.schema.json` defines extensible study-type/domain
  packs that can add constraints but cannot relax core policy.
- `schemas/project-profile.schema.json` separates per-project paths and
  adapters from the universal core.
- `core/ledger.py` validates core records and existing legacy events without
  writing data.
- `core/indexing.py` maps `core/1.0` records to the lexical and semantic
  retrieval projection while preserving the original record as canonical JSON.
- `mcp/research_memory/` provides local read-only candidate retrieval and
  evidence fetch with indexed-versus-current SHA-256 integrity status. It never
  exposes direct ledger writes.

## Codex marketplace

`marketplace_root/.agents/plugins/marketplace.json` is a repository-contained
local marketplace. Its plugin source is a relative link to the canonical plugin
directory, so it can be registered locally without copying plugin code. The
top-level `.agents` directory is workspace-managed and read-only.

## 상태

현재 단계는 원본 MCP와 DB 구조를 독립 프로젝트로 이주하는 bootstrap 단계다. 이 단계에서는 실험, benchmark, network, background watcher, remote 작업을 실행하지 않는다.

## 다음 단계

- claim, protocol, contribution, and audit projections as display adapters
- a separately approved append-only proposal/commit write boundary
- fixture-based contract expansion before any index builder integration
