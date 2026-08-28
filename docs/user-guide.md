# Universal Research MCP 사용자 설명서

이 문서는 `v0.9.3` 기준으로 Universal Research MCP를 처음 설치하고,
독립 연구 저장소를 만들고, Codex에서 근거를 검색·검증하고, 승인된 기록을
추가하는 전체 흐름을 설명한다.

Universal Research MCP는 검색 결과를 곧바로 사실로 취급하지 않는다. 검색은
후보를 찾고, 원문 조회는 등록된 파일·행 범위·SHA-256을 다시 확인하며, 근거
적격성 검사는 그 근거가 현재도 사용할 수 있는지만 판정한다. 자료의 의미,
관련성, 충돌, 진실성 및 최종 주장은 여전히 사용자가 검토해야 한다.

## 1. 지원 범위

| 항목 | v0.9.3 지원 상태 |
| --- | --- |
| 호스트 | 로컬 Codex |
| 전송 방식 | 로컬 `stdio` 권장 |
| 기본 검색 | lexical candidate retrieval |
| 선택 검색 | 로컬 semantic, hybrid, adaptive |
| 정본 저장소 | append-only JSONL |
| 파생 저장소 | 재생성 가능한 SQLite lexical/semantic index |
| 쓰기 | CLI 승인 경로 또는 MCP prepare/approve/commit 경로 |
| 원격 공개 운영 | 인증된 다중 사용자 서비스로 지원하지 않음 |
| 에이전트 생성 | 기본 0개, 별도 범위 공개와 명시적 승인이 필요 |

Python 3.11 이상이 필요하다. Windows, macOS, Linux에서 배포 검증을 수행하지만,
이 설명서는 로컬 셸과 Codex를 사용하는 흐름을 기준으로 한다.

## 2. 설치

전체 이름이나 짧은 별칭 중 하나를 설치한다. 두 패키지를 동시에 설치할 필요는
없다.

```bash
python -m pip install "universal-research-mcp==0.9.3"
```

또는:

```bash
python -m pip install "urmcp==0.9.3"
```

`urmcp`는 별도의 구현이 아니라 정확히 같은 버전의
`universal-research-mcp`를 설치하는 짧은 PyPI 별칭이다.

설치 상태를 확인한다.

```bash
universal-research --version
universal-research --help
```

정상이라면 첫 명령은 `0.9.3`을 출력한다. 명령을 찾지 못한다면 현재 터미널과
패키지를 설치한 Python 환경이 같은지 확인한다.

## 3. 빈 연구 프로젝트 만들기

연구 데이터는 소프트웨어 저장소와 분리된 새 폴더에 두는 것을 권장한다.

```bash
universal-research init ./my-research
universal-research doctor --root ./my-research
universal-research index status --root ./my-research
```

`init`은 빈 정본 JSONL과 현재 상태의 lexical index를 만든다. 기존 문서를
자동으로 탐색하거나 가져오지 않으며, semantic index도 자동으로 만들지 않는다.
따라서 초기 검색 결과가 비어 있는 것은 정상이다.

중요한 경로는 다음과 같다.

| 경로 | 역할 | 권위 |
| --- | --- | --- |
| `data/events/` | 승인된 연구 기록과 source registration | 정본 |
| 사용자가 등록한 원문 파일 | 근거 원문 | 정본 기록이 가리키는 외부 자료 |
| `data/index/research.sqlite` | lexical 검색 | 파생물 |
| `data/index/semantic.sqlite` | 선택적 semantic 검색 | 파생물 |
| `config/` | 검색 및 실행 정책 | 운영 설정 |
| `data/ingest-drafts/`, `data/audit/` | pending/consumed draft, transaction journal 및 audit | 프로젝트 운영 상태 |
| `.universal-research/` | 선택적 semantic 환경, 모델 snapshot 및 setup lock | 로컬 모델 상태 |

SQLite 파일은 검색을 위한 파생물이다. ingest 영수증은 기본적으로 프로젝트 밖의
사용자 state directory에 저장된다. 백업의 핵심은 정본 JSONL, 등록한 원문,
설정과 아직 끝나지 않은 운영 상태다. 파생 index만 백업하고 정본을 버리면 안 된다.

## 4. Codex에 MCP 연결하기

Codex 설정에 다음 로컬 서버를 등록한다. `cwd`는 방금 초기화한 연구 프로젝트의
절대 경로로 바꾼다.

```toml
[mcp_servers.universal_research]
command = "universal-research"
args = ["serve", "--no-auto-index"]
cwd = "/absolute/path/to/my-research"
```

`--no-auto-index`는 새 세션이 열릴 때 index를 자동으로 쓰지 않게 한다. 파생
index 갱신은 필요한 시점에 별도로 승인해 실행한다. 이 옵션을 빼면 일반 CLI
서버는 시작 시 자동 index 동작을 사용할 수 있다.

Codex 플러그인 번들을 사용하는 경우 저장소의
`plugin/universal-research-memory`가 같은 MCP와 두 Research Skill, 새 세션
범위 확인 hook을 함께 제공한다. 플러그인을 설치하거나 갱신한 뒤에는 새 작업을
열어야 새 도구와 Skill이 로드된다. 플러그인을 신뢰하도록 설정하는 행위와 실제
작업 권한 승인은 서로 다른 결정이다.

### 새 세션에서 처음 확인할 내용

Universal Research 플러그인이 로드된 새 세션에서는 작업을 시작하기 전에 다음
범위를 확인해야 한다.

> 이 세션은 현재 작업 폴더에서 호스트 셸을 사용하고, 작업 범위 내 파일
> 생성·수정은 허용하며, 외부 네트워크·다운로드는 매번 사전 승인,
> 에이전트 생성은 없음으로 시작할까요? 변경할 범위나 추가로 허용할 경로가
> 있으면 알려주세요.

사용자가 답하기 전에는 셸, MCP 연구 도구, 파일, 네트워크, 다운로드, 모델 및
에이전트를 실행하지 않는다. 이 대화상의 확인은 OS sandbox나 암호학적 승인
영수증이 아니며, canonical ingest 승인도 대신하지 않는다.

### 연결 확인

새 Codex 작업에서 범위를 확인한 다음 아래처럼 요청한다.

```text
Universal Research MCP 연결 상태와 현재 index 상태를 읽기 전용으로 확인해줘.
```

`research_index_status`가 응답하고 lexical 상태가 `current`이면 기본 연결이
정상이다. 빈 프로젝트에서 `memory_latest` 결과가 빈 배열인 것도 정상이다.

## 5. 읽기 전용 연구 흐름

일반적인 근거 확인은 다음 순서로 진행한다.

```text
후보 검색
  → 정확한 event/source locator로 원문 재조회
  → 현재 SHA-256과 등록 SHA-256 비교
  → 근거 적격성 확인
  → 사용자가 의미·충돌·출처 품질 검토
  → 답변 또는 보류
```

Codex에는 다음처럼 요청할 수 있다.

```text
"연구 질문"과 관련된 기록을 찾아줘. 후보 검색만으로 결론내리지 말고,
사용한 후보의 등록된 원문 범위와 SHA-256을 다시 확인해. 반환된
claim_gate_reference를 변경하지 말고 근거 적격성 검사에 사용한 다음,
관련성이나 자료 간 충돌은 별도로 설명해줘.
```

### 핵심 MCP 도구

| 도구 | 용도 | 쓰기 여부 |
| --- | --- | --- |
| `memory_search_candidates` | lexical/semantic/hybrid/adaptive 후보 검색 | 읽기 전용 |
| `memory_latest` | 최신 비-reference 기록 조회 | 읽기 전용 |
| `memory_fetch_evidence` | 정확한 등록 범위와 현재 파일 revision 재검증 | 읽기 전용 |
| `memory_check_evidence_eligibility` | 무결성·범위·근거 수 기준의 적격성 영수증 생성 | 읽기 전용 |
| `memory_audit_ledger` | canonical JSONL 무결성 및 정책 점검 | 읽기 전용 |
| `research_index_status` | 시작 시점과 현재 index 상태 조회 | 읽기 전용 |

### 검색 결과 해석

`memory_search_candidates`의 결과에는 `candidate_only: true`가 포함된다. 이는
검색 점수가 높더라도 아직 답변 근거가 아니라는 뜻이다. 결과의 event ID, 원문
경로, 행 범위와 등록 SHA-256을 이용해 `memory_fetch_evidence`를 호출해야 한다.

원문 조회에서 확인할 필드는 다음과 같다.

| 필드 | 의미 |
| --- | --- |
| `integrity_status: matched` | 현재 파일 bytes의 SHA-256이 등록 revision과 같음 |
| `range_valid: true` | 등록된 정확한 행 범위가 현재 파일에 존재함 |
| `canonical_locator_verified: true` | event ID를 포함한 완전한 locator가 확인됨 |
| `content_withheld: true` | revision 불일치로 본문을 기본 차단함 |
| `claim_gate_reference` | 근거 적격성 도구에 그대로 전달할 객체 |

행 주변 문맥은 화면 표시를 위한 정보다. `context_start_line`과
`context_end_line`을 새 근거 locator로 바꾸면 안 된다. 적격성 검사에는 반환된
`claim_gate_reference`를 수정하지 않고 전달한다.

`memory_check_evidence_eligibility`가 통과해도 `claim_verified`는 `false`다.
적격성 검사는 등록된 현재 근거의 무결성, 범위와 필요한 개수를 확인할 뿐,
문장이 그 근거로부터 논리적으로 도출되는지 또는 원문 자체가 사실인지는 판정하지
않는다.

## 6. 검색 모드 선택

`memory_search_candidates`의 `mode`로 검색 방식을 선택할 수 있다.

| 모드 | 사용 시점 | 요구 사항 |
| --- | --- | --- |
| `configured` | 일반적인 기본값 | 프로젝트 profile 또는 lexical fallback |
| `lexical` | 정확한 용어, ID, 파일명, 코드 식별자 | 현재 lexical index |
| `semantic` | 표현이 다른 유사 개념 | 구성되고 현재인 semantic index |
| `hybrid` | 키워드와 의미 유사도를 함께 사용 | lexical + semantic index |
| `adaptive` | 질의 형태에 따라 로컬에서 선택 | 사용 가능한 index와 profile |

모든 모드는 후보 검색이다. semantic 점수도 원문 재검증을 대신하지 않는다.
구성 상태는 다음 명령으로 확인한다.

```bash
universal-research index status --root ./my-research
universal-research semantic status --root ./my-research
```

## 7. 첫 원문과 기록 추가하기

다음 예시는 한 Markdown 원문과 하나의 관찰 기록을 추가한다. 모든 파일은 연구
프로젝트 안에 있어야 한다.

### 7.1 원문 만들고 revision 등록

```bash
mkdir -p ./my-research/docs
printf '# Note\n\nA verified observation.\n' > ./my-research/docs/note.md
universal-research source register docs/note.md --root ./my-research \
  --source-id src_note_v1 --source-type markdown
```

출력된 `source_sha256`을 기록한다. 파일을 나중에 수정하면 같은 revision이
아니므로, 기존 등록을 덮어쓰지 말고 새 source revision과 이를 가리키는 새
record를 만들어야 한다.

### 7.2 사람 승인 기록 추가

`approval.json`을 다음과 같이 작성한다. 시간과 ID는 실제 작업에 맞춰 바꾼다.

```json
{
  "schema_version": "core/1.0",
  "record_id": "approval_note",
  "record_kind": "approval",
  "study_id": "study_demo",
  "occurred_at": "2026-08-27T10:00:00+09:00",
  "recorded_at": "2026-08-27T10:00:00+09:00",
  "status": "approved",
  "created_by": {
    "actor_id": "actor_owner",
    "actor_type": "human"
  },
  "payload": {
    "scope": {
      "study_ids": ["study_demo"],
      "record_kinds": ["observation"]
    }
  }
}
```

승인 ID를 화면에서 직접 확인한 뒤 같은 ID로 append를 승인한다.

```bash
universal-research record approve approval.json --root ./my-research \
  --confirm approval_note
```

### 7.3 관찰 기록 검증 후 추가

`observation.json`을 작성한다. `<source_sha256>`은 7.1에서 반환된 값으로
교체한다. `start`와 `end`는 실제 원문 범위와 일치해야 한다.

```json
{
  "schema_version": "core/1.0",
  "record_id": "observation_note",
  "record_kind": "observation",
  "study_id": "study_demo",
  "occurred_at": "2026-08-27T10:05:00+09:00",
  "recorded_at": "2026-08-27T10:06:00+09:00",
  "status": "completed",
  "created_by": {
    "actor_id": "actor_owner",
    "actor_type": "human"
  },
  "approval_refs": ["approval_note"],
  "source_refs": [
    {
      "artifact_revision_id": "artifact_note@sha256:<source_sha256>",
      "locator": {
        "kind": "line_range",
        "path": "docs/note.md",
        "start": 1,
        "end": 3
      },
      "verification_status": "integrity_verified"
    }
  ],
  "artifact_refs": ["artifact_note"],
  "payload": {
    "summary": "Verified input record"
  }
}
```

먼저 쓰기 없는 검증을 수행하고, 성공한 같은 파일을 승인 reference와 함께
append한다.

```bash
universal-research record validate observation.json --root ./my-research
universal-research record append observation.json --root ./my-research \
  --approval-ref approval_note
```

성공한 source 또는 record append는 lexical index 갱신도 시도한다. canonical
append는 성공했지만 index 갱신이 실패한 경우 명령은 stale 상태를 보고한다.
그때는 원인을 확인한 뒤 다음 갱신을 별도로 승인한다.

```bash
universal-research index ensure --kind lexical --root ./my-research
```

더 작은 출발점이 필요하면 `universal-research record template`으로 기본 record
형식을 출력할 수 있다. template은 승인 우회 수단이 아니다.

## 8. MCP를 통한 승인형 ingest

Codex가 새 record를 제안하게 하려면 CLI 직접 append 대신 다음 3단계를 사용할
수 있다.

1. `research_prepare_ingest`가 record와 source registration을 검증하고 변경 불가능한
   pending draft를 만든다. 이 단계는 canonical JSONL을 수정하지 않는다.
2. 사용자가 draft ID, SHA-256, record ID, canonical head 및 source 수를 검토한 뒤
   별도 CLI로 한 번만 쓸 수 있는 서명 영수증을 발급한다.
3. `research_commit_ingest`가 정확히 그 draft와 영수증만 소비해 append한다.

준비 단계에는 기존 human approval record가 필요하다. source registration은
`path`, `source_id`, `source_type`만 받을 수 있다.

draft 상태는 본문을 노출하지 않는 `research_pending_ingest_status`로 확인할 수
있다. 검토가 끝나면 별도 호스트 셸에서 다음 명령을 실행한다.

```bash
universal-research ingest approve --root ./my-research \
  --draft-id ingest_... \
  --draft-sha256 <draft-sha256> \
  --confirm-draft-sha256 <draft-sha256> \
  --expires-at 2026-08-27T12:00:00+09:00
```

반환된 receipt ID와 정확한 draft ID/SHA-256을
`research_commit_ingest`에 전달한다. commit 도구는 record 본문이나
`approved: true` 같은 모델 생성 승인 값을 받지 않는다. draft 변경, canonical
head 변경, source 변경, 만료·재사용 영수증은 거부한다.

pending 파일이나 lock을 직접 수정해 실패를 우회하지 않는다. 중간 실패가
발생하면 같은 승인된 transaction의 상태를 먼저 확인하고, 보고된 복구 경로를
따른다.

## 9. 선택적 semantic 검색

semantic 검색은 기본 설치 후 자동으로 활성화되지 않는다. 기능 흐름만 확인하려면
외부 모델이 필요 없는 deterministic demo backend를 구성할 수 있다.

```bash
universal-research semantic configure --backend demo --root ./my-research
universal-research semantic build --root ./my-research
universal-research semantic status --root ./my-research
```

demo backend는 수명 주기와 검색 통합 확인용이며 모델 품질을 대표하지 않는다.

실제 로컬 SentenceTransformer snapshot을 사용하려면 먼저 검토된 모델 목록을
확인한다.

```bash
universal-research semantic models
```

setup 계획에는 이동하는 branch나 tag가 아니라 모델 저장소의 전체 40자리 commit
SHA가 필요하다.

```bash
universal-research semantic setup --root ./my-research \
  --model intfloat/multilingual-e5-base \
  --revision '<full-40-character-commit-sha>'
```

첫 호출은 계획만 만든다. 출력된 package, 다운로드 위치, revision, 환경 및 plan
SHA-256을 검토한다. 설치와 모델 다운로드는 각각 외부 네트워크·다운로드 승인을
받은 뒤, 같은 인수와 아래 옵션으로 실행한다.

```bash
universal-research semantic setup --root ./my-research \
  --model intfloat/multilingual-e5-base \
  --revision '<full-40-character-commit-sha>' \
  --execute --confirm-plan-sha256 '<displayed-plan-sha256>'
```

기존 snapshot 재사용도 `--reuse-existing`만으로 신뢰하지 않는다. manifest의 모델
ID, revision, 파일 목록, 크기와 SHA-256이 모두 일치해야 한다. 자세한 제약은
[semantic retrieval 문서](semantic-retrieval.md)를 참고한다.

## 10. 운영 명령 요약

| 목적 | 명령 |
| --- | --- |
| 버전 확인 | `universal-research --version` |
| 프로젝트 초기화 | `universal-research init <path>` |
| 전체 준비 상태 | `universal-research doctor --root <path>` |
| index 상태 | `universal-research index status --root <path>` |
| lexical index 복구 | `universal-research index ensure --kind lexical --root <path>` |
| semantic 상태 | `universal-research semantic status --root <path>` |
| source 등록 | `universal-research source register ...` |
| record 형식 출력 | `universal-research record template` |
| record 검증 | `universal-research record validate ...` |
| human approval 추가 | `universal-research record approve ...` |
| 승인된 record 추가 | `universal-research record append ...` |
| pending draft 영수증 | `universal-research ingest approve ...` |
| 관측 token 사용량 | `universal-research usage summary --root <path>` |
| MCP 서버 | `universal-research serve --no-auto-index` |

각 하위 명령의 정확한 인수는 `--help`로 확인한다.

```bash
universal-research source register --help
universal-research record append --help
universal-research ingest approve --help
universal-research serve --help
```

## 11. 자주 발생하는 문제

| 증상 | 의미 | 조치 |
| --- | --- | --- |
| 검색 결과가 없음 | 새 프로젝트이거나 아직 record가 없음 | source와 승인된 record를 먼저 추가한다. |
| `stale` index | canonical JSONL과 파생 index가 다름 | writer가 없는지 확인하고 `index ensure`를 승인한다. |
| `integrity_status: mismatched` | 등록 후 원문 bytes가 바뀜 | 기존 근거로 결론내리지 말고 새 revision과 record를 등록한다. |
| exact range 오류 | 후보의 완전한 locator와 다른 범위를 요청함 | event ID, path, 시작/끝 행, SHA-256을 후보 그대로 사용한다. |
| multiple revisions 오류 | 같은 path의 revision을 하나로 정할 수 없음 | event ID와 expected SHA-256을 함께 전달한다. |
| semantic `missing` | 아직 semantic backend를 구성하지 않음 | lexical을 사용하거나 명시적으로 semantic을 구성한다. |
| lock 오류 | 다른 writer 또는 종료된 작업의 lock 가능성 | writer 존재 여부와 journal을 확인하기 전 lock을 삭제하지 않는다. |
| receipt 만료·재사용 오류 | one-time ingest 권한이 유효하지 않음 | pending 상태를 다시 확인하고 사람 검토 후 새 영수증 경로를 따른다. |
| 플러그인 갱신이 보이지 않음 | 현재 작업이 이전 plugin snapshot을 사용함 | plugin을 다시 설치한 뒤 새 Codex 작업을 연다. |
| MCP 명령을 찾지 못함 | Codex 환경의 PATH와 설치 환경이 다름 | Codex가 실행하는 환경에서 `universal-research --version`을 확인한다. |

원문 불일치 상황에서 `allow_mismatched_content`는 진단용 본문 표시일 뿐 근거
적격성을 복구하지 않는다. 불일치를 강제로 통과시키는 옵션으로 사용하면 안 된다.

## 12. 보안 및 운영 경계

- 연구 root는 쓰기 가능한 독립 프로젝트로 지정한다. 참고 원본 프로젝트나 다른
  연구의 DB를 같은 runtime root로 사용하지 않는다.
- `stdio` 로컬 연결을 권장한다. `streamable-http`를 인터넷에 직접 노출하는 것은
  인증된 서비스 배포가 아니다.
- `.env`, credential, private key 같은 민감 파일은 source로 등록하지 않는다.
- 파일 생성·수정 승인과 canonical ingest 승인은 다르다. 일반 파일 권한으로
  연구 ledger append를 자동 승인하지 않는다.
- 외부 네트워크, 다운로드, package 설치, 모델 실행, benchmark 및 agent 생성은
  각각 범위와 비용을 공개하고 별도 승인을 받는다.
- hash 일치는 파일 revision의 동일성만 나타낸다. 출처의 진실성이나 독립성은
  나타내지 않는다.

더 자세한 경계는 [보안 모델](security.md), [호스트 통합 계약](host-integration.md),
[입력 CLI 튜토리얼](input-cli-tutorial.md)에서 확인할 수 있다.

## 13. 업데이트와 인용

패키지를 새 버전으로 올리기 전에는 연구 root를 백업하고 release note의 schema 및
운영 변경을 확인한다.

```bash
python -m pip install --upgrade "universal-research-mcp==0.9.3"
universal-research --version
universal-research doctor --root ./my-research
```

패키지를 제거해도 연구 프로젝트가 자동으로 삭제되지는 않는다. 반대로 연구 root를
삭제하면 PyPI 패키지를 다시 설치해도 canonical 기록은 복원되지 않는다.

Zenodo DOI가 연결된 release를 인용할 때는 재현 대상이 특정 버전이면 버전 DOI를,
항상 최신 release를 가리키려면 concept DOI를 사용한다. DOI가 발급되기 전에는
README에 임의의 DOI나 badge를 넣지 않는다.

---

`v0.9.0`은 동작이 바뀌는 release다. 서버가 세션의 근거 fetch 이력을 기록하고,
무결성 검사에 실패한 근거를 조용히 누락한 material claim은 적격성 영수증이
`blocked`(`OMITTED-MISMATCHED-EVIDENCE`)로 내려간다 — 공개만으로는 모델이
경고를 무시한다는 실측(9/13 무시)에 근거한 fail-closed 설계다
(benchmarks/results/rebench-v1.2-v1.3-citation-discipline-20260829.md).
routine claim은 공개만 받는다. 이전과 동일하게, 적격성 검사는 무결성·범위·
개수 검사이며 사실 판정이나 일반적인 환각 감소를 보증하지 않는다.
