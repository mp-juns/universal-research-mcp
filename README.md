# Universal Research MCP

연구 검색 결과를 곧바로 사실로 취급하지 않고 계획·승인·관찰·주장·실패·수정·기여를
출처와 함께 추적하는 append-only 연구 운영 프레임워크이자 provenance-first MCP다.
Canonical JSONL은 권위 원본이며 SQLite 검색 DB는 검증 후 재생성할 수 있는 파생물이다.

> **지원 범위 (0.3.0 preview): 현재 공개 통합 대상은 Codex 플러그인뿐이다.**
> 모델 선택, 실제 에이전트 세션, 도구 실행과 승인은 Codex host가 소유한다.
> Ollama·OpenAI API·Anthropic API·Moonshot/Kimi 모델 실행과 Claude Code·OpenCode·
> OpenClaw 통합은 이번 공개본에서 지원하거나 자동 호출하지 않는다.

저장소에는 후속 연구용 provider/runtime prototype이 남아 있을 수 있으나 기본 CLI,
Codex 플러그인, MCP 도구 또는 배포 entry point로 노출되지 않는다. 이 내부 코드는
호환성·지원 계약이 아니며 현재 공개 기능으로 간주하면 안 된다.

## Quick Start (5 minutes)

Python 3.11 이상이 필요하다. PyPI 설치 후 독립 연구 저장소와 검색 DB를 초기화한다.

```bash
python -m pip install universal-research-mcp
universal-research init ./my-research
universal-research serve --root ./my-research
```

초기화는 빈 `sources.jsonl`, 무결성 검증된 FTS5 DB, 빈 semantic DB를 만든다.
Canonical JSONL을 수정하지 않는 lexical refresh는 staging DB 검증 후 원자적으로
교체된다. 프로젝트 경로는 `--root` 또는 `UNIVERSAL_RESEARCH_ROOT`로 지정한다.

기본 `universal_research` MCP는 다음만 제공한다.

- 근거 후보 검색과 event/hash가 결합된 원문 재검증
- 고정 11-role governance contract와 Codex dispatch manifest 준비
- scope/cost preflight, deterministic operation gate, failure tombstone 준비
- lexical/semantic 파생 index 상태

MCP는 사용자 대신 승인하거나 canonical ledger를 쓰지 않으며 모델·API·benchmark를
호출하지 않는다. 현재 query-time 검색은 lexical mode만 지원한다. Dense embedding
생성과 외부 provider fallback은 후속 릴리스 범위다.

## Minimal evidence flow

```text
canonical JSONL → staged/verified SQLite candidates → memory_fetch_evidence
with event_id + expected_sha256 → current hash check → bounded claim
```

검색 후보는 근거가 아니다. 중요한 결론에는 `memory_fetch_evidence`가 반환한 원문
event ID·path·line range·expected/current hash·`integrity_status`를 함께 남긴다.
인덱스에 등록되지 않은 프로젝트 파일은 fetch할 수 없다.

## Governed Codex agents

`scope_and_cost_governor`는 모든 mode에서 계획 승인 전에 실행된다. 필요성, 시간 범위,
작업 단위, 난이도, compute/network 비용과 근거를 평가하지만 직접 승인하거나 프로세스를
종료하지 않는다. 실제 allow/block은 승인된 `scope_hash`와 tool call을 비교하는
결정론적 controller가 수행한다.

플러그인은 role별 task packet, hash-bound scope receipt, 동일 evidence snapshot을 가진
Codex dispatch manifest를 준비한다. manifest 자체는 에이전트를 시작하지 않는다.
Codex가 host 권한과 사용자 entitlement 안에서 실제 subagent를 만들고 병렬 실행하며,
세션·모델·GUI 표시 여부도 Codex surface가 결정한다. 플러그인이 별도 유료 API로
우회하거나 Codex 구독 권한을 외부 API quota로 바꾸지 않는다.

실패 정책 기본값은 `blocking_only + ask + redacted`다. 실패 사실과 최소 tombstone은
항상 남고 상세 보존만 `full | metadata_only | ask` 및
`full | redacted | hashes_only`로 조절한다. `off` 모드는 없다.

Codex host visualization은 기본값이 `off`다. task capability scope, plan reference,
사용자의 명시적 opt-in이 모두 있을 때만 별도로 사용할 수 있다. 일반 데이터 plot
권한은 host visualization 권한을 뜻하지 않는다.

## 목적

- 연구 계획, 승인, 실행, 실패, 결정, 결과, 출처를 추적하는 공통 운영 규칙을 제공한다.
- 기존 연구 프로젝트의 BM25/FTS5 및 dense embedding DB 구조를 참고해 재현 가능한 연구 기억 저장소를 설계한다.
- `memory_search_candidates`, `memory_latest`, `memory_fetch_evidence` 같은 source-grounded MCP 도구를 범용화한다.
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

- `mcp/project_search/`의 legacy MCP proxy, API server, query expansion, export 코드
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
  writing data. Its safety-relevant checks have JSON Schema parity fixtures.
- `core/indexing.py` maps `core/1.0` records to the lexical and semantic
  retrieval projection while preserving the original record as canonical JSON.
- `mcp/research_memory/` provides local read-only candidate retrieval and
  evidence fetch with indexed-versus-current SHA-256 integrity status. It never
  exposes direct ledger writes.
- `core/governance.py` defines the fixed eleven-role research governance roster,
  mode activation, task/decision validation, claim escalation, and concise
  central-manager reporting without executing models or experiments.
- `core/index_refresh.py` permits only canonical-event-triggered derived-index
  refreshes and validates index-health records; it never rewrites canonical
  evidence. See `docs/multi-agent-governance.md`.

## Codex marketplace

`marketplace_root/.agents/plugins/marketplace.json` is a repository-contained
local marketplace. Its plugin source is a relative link to the canonical plugin
directory, so it can be registered locally without copying plugin code. The
plugin requires the PyPI package to be installed first and calls its
`universal-research` entry point; it does
not reach back to `../../mcp` or `../../data`. The top-level `.agents` directory
is workspace-managed and read-only.

## 상태

현재 공개 지원 범위는 Codex 플러그인, 로컬 lexical lifecycle, source-grounded evidence
fetch, 11-role governance, 비실행 Codex dispatch contract다. 설치·기본 MCP 시작·CI는
API 호출, local model call, 모델 download, benchmark, background watcher를 실행하지
않는다. 내부 provider/runtime prototype은 기본 공개면에서 비활성화되어 있다.

## 다음 단계

- top-level `core`/`adapters` compatibility packages의 차기 namespaced migration
- 영어 기본 README와 한국어 `README.ko.md` 분리
- Codex host dispatch 통합 fixture와 설치 문서 강화
- 별도 설계·보안 검토 후 local/OpenAI/Anthropic/Moonshot provider adapter 평가
- Claude Code·OpenCode·OpenClaw용 host adapter는 각각 독립 지원 계약으로 검토
