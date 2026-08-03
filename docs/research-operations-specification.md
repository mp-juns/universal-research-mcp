# Universal Research Operations Framework Specification

## 1. 목적과 비목적

Universal Research Memory MCP는 특정 모델, 실험 분야, 논문 주제의 지식을
내장하려는 시스템이 아니다. 이 프레임워크의 목적은 사람·AI·외부 시스템이
수행하는 연구 활동을 **출처가 있는 append-only 기록**으로 운영하고, 검색과
자동화를 연구 근거의 권위 구조 아래에 두는 것이다.

이 시스템이 관리하는 대상은 다음과 같다.

- 연구 계획, protocol, approval, execution session
- 원자료·파생자료·artifact revision 및 lineage
- Expected, Observed, Interpretation, Uncertainty의 분리
- claim과 원문 evidence의 연결
- human, AI, external system의 기여와 책임 분리
- amendment, negative result, stopped work의 보존
- 재현성 fingerprint와 사후 조건 변경 추적
- 분야별 pack과 프로젝트별 profile의 안전한 확장

다음은 의도적으로 제공하지 않는다.

- semantic similarity만으로 사실·인과·성능 우위를 확정하는 기능
- 승인, canonical ledger write, amendment를 MCP tool로 직접 실행하는 기능
- 모델 loading, benchmark, daemon, remote proxy를 기본 MCP에 노출하는 기능
- 특정 reference project의 모델 수치·장치 조건·결과를 범용 규칙으로 일반화하는 기능

## 2. 계층 구조

```text
Universal Research Core
  ├─ governance vocabulary and record contract
  ├─ append-only amendment and approval boundary
  ├─ audit rules and provenance validation
  └─ Core-to-index projection
        ↓
Study-Type / Domain Packs
  └─ core policy를 완화하지 않는 추가 제약
        ↓
Project Profile
  └─ project paths, adapter selection, reference boundary
        ↓
Execution Session
  └─ approved scope 안의 사람·AI·외부 시스템 활동
        ↓
Storage / Search / Display adapters
  ├─ JSONL canonical ledger
  ├─ SQLite FTS / optional semantic index
  ├─ Markdown plan/work-log projection
  └─ Obsidian 등 표시 adapter
        ↓
Read-only MCP and Codex Skill
```

Core는 특정 파일명이나 directory path에 의존하지 않는다. `TODO.md`와
`WORK_LOG.md`는 범용 개념이 아니라 display adapter다. JSONL은 canonical event
stream의 backend이고, SQLite와 embedding index는 derived retrieval view다.

## 3. 권위와 provenance 모델

권위는 다음 순서로 해석한다.

1. append-only canonical JSONL record
2. 해당 record가 가리키는 original artifact와 artifact revision hash
3. lexical SQLite 및 optional semantic index
4. MCP search candidate
5. Markdown TODO, WORK_LOG, session note

따라서 SQLite, vector index, Markdown view는 언제든 재생성·수정될 수 있지만
canonical ledger를 대체할 수 없다. 검색 결과는 후보 metadata이고, load-bearing
conclusion은 반드시 원문 artifact의 path, line/page/row range, 현재 hash를 다시
확인해야 한다.

```text
canonical JSONL
  → derived index candidate
  → source-line fetch
  → indexed/current hash integrity check
  → bounded claim, decision, or audit finding
```

## 4. Core record contract

Core record의 schema version은 `core/1.0`이다.

```json
{
  "schema_version": "core/1.0",
  "record_id": "claim_example",
  "record_kind": "claim",
  "study_id": "study_example",
  "occurred_at": "2026-08-04T10:00:00+09:00",
  "recorded_at": "2026-08-04T10:01:00+09:00",
  "status": "completed",
  "created_by": {
    "actor_id": "actor_researcher",
    "actor_type": "human"
  },
  "relations": [],
  "source_refs": [],
  "artifact_refs": [],
  "approval_refs": [],
  "payload": {}
}
```

### 4.1 Record kinds

- `research_plan`: 목적, scope, exclusion, success criterion
- `protocol`: 사전 정의된 측정·처리·분석 절차
- `approval`: human authority와 명시적 scope
- `execution_session`: 승인된 실행 단위
- `observation`: 측정·관찰된 사실
- `decision`: 근거를 바탕으로 한 선택
- `claim`: 지지·반박 상태를 가진 주장
- `artifact`: 데이터·문서·코드·산출물의 식별
- `amendment`: 원기록을 덮어쓰지 않는 제한적 정정
- `audit_finding`: rule violation 또는 검증 누락
- `contribution`: 사람·AI·외부 기여의 귀속
- `negative_result`: 기대와 다른 또는 유의하지 않은 결과
- `stopped_work`: 중단 사유와 잔여 불확실성

### 4.2 구조적 제약

validator는 다음을 확인한다.

- record ID, study ID, actor ID, protocol/approval/artifact/contribution reference 패턴
- ISO 8601 date-time
- 허용되지 않은 top-level, actor, relation, evidence field의 거부
- `created_by.actor_type`이 `human`, `ai`, `external_system` 중 하나인지 여부
- relation type 및 `target_id` 존재 여부
- artifact revision이 `artifact_...@sha256:<64 hex>` 형식인지 여부
- evidence locator가 비어 있지 않은 object인지 여부
- approval/artifact/contribution reference array의 중복 여부

JSON Schema와 dependency-free manual validator는 representative parity fixture로
함께 검증된다. Schema의 format vocabulary가 환경별로 annotation으로 처리되지
않도록 test dependency는 명시적 `date-time` format checker를 사용한다.

## 5. Expected / Observed / Interpretation / Uncertainty

연구 기록은 다음 네 층을 의도적으로 분리한다.

| 층 | 의미 | 금지되는 혼합 |
|---|---|---|
| Expected | 사전 가설, 계획, 예측 | 측정 결과처럼 기록 |
| Observed | 실제 측정·실행·검증 사실 | 해석을 사실로 기록 |
| Interpretation | 관찰에 대한 설명 또는 모델 | 인과·우월성을 확정 |
| Uncertainty | 한계, 누락, 조건, 미검증 요소 | 결과에서 삭제 |

`claim`의 `support_status: "supported"`는 human-verified evidence가 있을 때만
validator를 통과한다. 관찰되지 않은 기대나 semantic similarity는 supported claim의
근거가 될 수 없다.

## 6. Evidence와 artifact lineage

Evidence reference는 artifact revision과 locator를 함께 가진다.

```json
{
  "artifact_revision_id": "artifact_result@sha256:<sha256>",
  "locator": {
    "kind": "line_range",
    "path": "docs/result.md",
    "start": 10,
    "end": 24,
    "heading": "Observed result"
  },
  "verification_status": "human_verified"
}
```

relation은 protocol, artifact, observation, claim, decision 사이의 lineage를
표현한다. 예시는 `uses_protocol`, `generated_from`, `derived_from`,
`supported_by`, `refuted_by`, `validated_by`, `corrects`, `supersedes`다.

Core-to-index adapter는 relation의 `target_id`, source path, artifact revision
hash를 derived projection으로 보존한다. SQLite의 `raw_json`에는 변환 전 Core
record가 남아 있으므로 index가 원본 provenance를 덮어쓰지 않는다.

## 7. Approval과 execution governance

canonical ledger append는 MCP가 아닌 별도 approval-checked helper를 통해서만
가능하다. append 전에는 다음이 모두 충족돼야 한다.

1. record가 approval reference를 명시한다.
2. 해당 approval record가 기존 ledger에 실제 존재한다.
3. referenced record의 kind가 `approval`이고 status가 `approved`다.
4. 승인 주체의 actor type이 `human`이다.
5. approval payload가 explicit scope를 가진다.
6. scope가 직접 record ID를 포함하거나, 해당 study와 record kind를 포함한다.
7. draft/proposed record는 canonical append 대상이 아니다.

`execution_session`이 active 상태가 되려면 explicit approval이 필요하다. 이
규칙은 “에이전트가 실행할 수 있는가”와 “검색할 수 있는가”를 분리한다. MCP는
검색만 할 수 있고, 실행 허가나 기록 append 권한을 얻지 못한다.

## 8. Amendment, negative result, stopped work

원기록은 수정하지 않는다. amendment는 append-only record이며 다음을 요구한다.

- 정확히 하나의 `corrects` relation
- `/payload/...` 아래의 제한된 field만 대상
- recorded value, corrected value, reason 동시 기록
- completed amendment만 resolved retrieval view에 적용

negative result와 stopped work는 실패를 숨기는 log가 아니라 별도 canonical
record다. 이 구조는 사후 선택, 조건 변경, 중단 사유, 재시도 가능성을 보존한다.

## 9. 에이전트 운영 헌법

`AGENTS.md`와 `agents/AGENT_RULES.md`는 구현 규칙이 아니라 연구 운영의
control plane이다.

### 9.1 명시적 승인 없이 금지되는 행동

- shell command 실행
- 파일 생성·수정·복사·삭제
- package install
- test/build
- network, remote, background 작업
- benchmark, model download, daemon 실행

### 9.2 비자명한 작업의 사전 기록

작업 전에는 다음을 TODO/WORK_LOG projection에 남긴다.

- 요청자, 제안자, 결정자, 계획자, 실행자, 검증자
- 대상 파일과 정확한 command
- 성공 기준과 제외 범위
- reference project 및 secret boundary

unexpected result가 나오면 즉시 중단하고, 원인을 분리하고, 계획을 갱신한 뒤
승인 범위 안에서만 재개한다. 즉 에이전트는 실패를 숨기거나 자동으로 scope를
확장할 수 없다.

### 9.3 책임과 contribution

`created_by`는 human, AI, external system을 구분한다. AI-authored decision에
human reviewer가 없으면 read-only audit가 finding을 생성한다. 이 규칙은 AI의
작업을 사용자 연구 성과로 자동 귀속하지 않도록 한다.

## 10. 환경 격리와 reference-project boundary

reference project와 universal framework runtime은 분리된다.

```text
Reference project
  ├─ schema, adapter design 참고를 위한 read-only input
  ├─ historical result / session / DB / index 수정 금지
  └─ runtime DB 공유 금지

Universal Research MCP project
  ├─ independent JSONL ledger
  ├─ independent derived indexes
  ├─ independent TODO / WORK_LOG / session view
  ├─ independent package, plugin, CI, release
  └─ Project Profile 기반 runtime path
```

금지되는 작업:

- reference project의 JSONL, SQLite, embedding index에 write
- reference DB를 새 MCP의 runtime DB로 연결
- 원본 TODO/WORK_LOG와 framework 작업 로그를 혼합
- 기존 모델 수치, device 조건, benchmark 결과를 universal policy로 승격

## 11. Path와 secret boundary

Core는 고정 경로를 모른다. runtime path는 Project Profile, CLI, 또는
environment configuration이 제공한다.

```bash
universal-research-mcp \
  --root /path/to/research-project \
  --lexical-db /path/to/research.sqlite \
  --events-root /path/to/data/events
```

MCP evidence fetch는 다음을 차단한다.

- absolute path와 `..` traversal
- `.env`, token, credential, secret, private key 계열 이름
- project root 밖으로 탈출하는 symlink

`.gitignore`는 workspace-managed agent state, cache, bytecode, derived index,
semantic manifest 등을 public source에서 제외한다. API key와 secret은 ledger,
artifact, config example, Git commit에 기록하지 않는다.

## 12. Storage, search, display adapters

### 12.1 Canonical storage

`data/events/`의 JSONL이 append-only authority다. malformed JSONL은 index
builder에서 fail-closed 된다.

### 12.2 Lexical adapter

SQLite FTS5 index는 canonical JSONL에서 생성한다. event, relation, artifact,
source range, source hash, raw JSON을 저장한다. legacy event와 Core record는
명시적으로 구분되고, 각각 legacy source-range correction과 Core amendment
resolution을 적용한다.

### 12.3 Optional semantic adapter

semantic runtime은 기본 MCP dependency가 아니다.

```bash
python -m pip install 'universal-research-mcp[semantic]'
```

semantic adapter는 sentence-transformers, torch, transformers,
huggingface-hub, safetensors, numpy를 opt-in extra로 제공한다. Core payload의
Expected/Observed/Interpretation/Uncertainty와 source passage를 retrieval text로
만들 수 있지만, model download, snapshot selection, semantic index build,
benchmark는 별도 승인 범위가 필요하다.

semantic index도 canonical truth가 아니며 similarity는 candidate ranking일 뿐이다.

### 12.4 Display adapter

Markdown TODO, WORK_LOG, plan view, work-log view, Obsidian류 도구는 사람이
읽기 위한 projection이다. display 변경은 canonical record 변경을 의미하지 않는다.

## 13. Read-only MCP contract

설치 및 실행:

```bash
python -m pip install universal-research-mcp
universal-research-mcp --root /path/to/research-project
```

도구:

- `memory_search_candidates`: lexical candidate retrieval
- `memory_latest`: 최신 non-reference record 조회
- `memory_fetch_evidence`: 원문 line range와 hash integrity 확인
- `memory_audit_ledger`: read-only policy and integrity findings
- `research_search`, `research_latest`, `research_fetch`: 기본 비노출 compatibility alias. 구형 client가 필요한 경우에만 `--legacy-tools` 또는 `UNIVERSAL_RESEARCH_ENABLE_LEGACY_TOOLS=1`로 활성화한다.

현재 공개 MCP query schema는 lexical mode만 허용한다. Semantic index는 별도 관리
CLI가 원자적으로 생성·검증하지만, query-time encoder/ranker가 연결되기 전에는
semantic/hybrid 요청을 빈 성공 결과로 반환하지 않는다.

제공하지 않는 도구:

- canonical write, approval, amendment
- shell execution
- model loading, semantic build, daemon control
- remote network proxy

### 13.1 Evidence integrity response

```json
{
  "path": "docs/evidence.md",
  "start_line": 10,
  "end_line": 24,
  "indexed_sha256": "...",
  "current_sha256": "...",
  "integrity_status": "matched",
  "content": "10: ..."
}
```

`integrity_status` 값:

- `matched`: index 당시 artifact와 현재 artifact가 동일
- `mismatched`: index 이후 artifact가 변경됨
- `not_indexed`: path에 index hash가 없음
- `ambiguous`: path에 복수 index hash가 있음

fetch는 source를 한 byte snapshot으로 읽고 content rendering과 current SHA-256을
그 snapshot에서 함께 계산한다. 따라서 읽기와 hash 계산 사이의 파일 교체로 서로
다른 버전을 반환하는 경쟁 조건을 줄인다.

## 14. Codex plugin과 Skill

plugin은 repository-relative `../../mcp/...` launcher에 의존하지 않는다.

```json
{
  "command": "universal-research-mcp",
  "args": []
}
```

plugin Skill의 의도된 workflow는 다음과 같다.

1. candidate search
2. 반환된 source path와 range로 evidence fetch
3. indexed/current hash와 integrity status 확인
4. uncertainty와 verification limit 명시
5. write, approval, execution이 필요하면 MCP 밖의 승인 boundary로 이동

## 15. Validation과 failure-mode coverage

fixture 및 CI가 다음을 확인한다.

- schema/manual validator parity
- fabricated approval, AI-issued approval, out-of-scope approval 거부
- Core claim → index → MCP candidate → evidence fetch → audit E2E
- source relation과 raw provenance 보존
- indexed/current hash match 및 index 이후 mutation mismatch
- sensitive path와 symlink escape 거부
- malformed JSONL fail-closed
- 1,000-record fixture의 canonical input byte 보존
- package console entry point와 plugin path independence
- lexical index regression

semantic encoder 전체 smoke/compatibility test는 heavy optional runtime을
필요로 하므로 기본 CI와 분리된다.

## 16. Package, CI, release

package 이름은 `universal-research-mcp`다.

- 기본 dependency: read-only MCP runtime
- `test` extra: JSON Schema format provider, numpy, pytest
- `semantic` extra: optional encoder runtime
- console command: `universal-research-mcp`

GitHub Actions CI는 push와 pull request마다 package install, console entry point,
Core, approval, MCP, lexical fixture를 실행한다. CI는 semantic encoder, model
download, benchmark, reference project를 실행하지 않는다.

PyPI release workflow는 GitHub Release published event에서만 실행된다.

1. source distribution과 wheel build
2. `twine check`
3. GitHub OIDC Trusted Publishing으로 PyPI publish
4. digital attestation 생성

release workflow는 `contents: read`와 `id-token: write`만 사용하며 PyPI API token을
repository secret, source, log에 저장하지 않는다.

## 17. 운영 요약

이 프레임워크의 핵심 규칙은 다음 한 문장으로 요약된다.

> AI는 연구 기록을 찾고, 구조화하고, 감사할 수는 있지만, 검색 결과를 근거로
> 바꾸거나 승인 범위를 넘어서 실행하거나, 원본 연구 환경을 침범할 수는 없다.

따라서 이 프로젝트는 단순 embedding DB나 memory MCP가 아니라, 연구 provenance,
approval, environment isolation, reproducibility, negative-result preservation을
함께 다루는 연구 운영 control plane이다.
