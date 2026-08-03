# Universal Research MCP Agent Rules

## 1. 승인과 기록

- 사용자의 명시적 승인 없이 명령 실행, 파일 수정, 파일 복사, package 설치, test/build, network, background, remote 작업을 하지 않는다.
- 비자명한 작업은 계획·정확한 명령·대상 파일·성공 기준·제외 범위를 먼저 기록하고 실행한다.
- 새 명령이나 새 capability가 필요하면 중단하고 다시 승인받는다.
- `TODO.md`와 `WORK_LOG.md`에는 요청자, 제안자, 결정자, 계획자, 실행자, 검증자를 구분한다.

## 2. 기존 연구 폴더의 read-only 경계

- Project Profile이 지정한 reference project는 참고 원본이다.
- 원본에 쓰기·삭제·이동·로그 append를 하지 않는다.
- 원본 DB를 새 MCP runtime DB로 직접 공유하지 않는다.
- 원본의 embedding DB는 schema·metadata·adapter 설계 참고로만 읽는다.

## 3. 독립 저장소

- canonical event는 `data/events/`의 append-only JSONL에 기록한다.
- lexical/dense SQLite는 `data/index/`에 생성하며 재생성 가능한 파생물이다.
- secret, API key, virtual environment, 모델 binary는 저장소에 복사하지 않는다.
- 원본 historical result와 새 프로젝트 작업 기록을 섞지 않는다.

## 4. 연구 근거

- 검색 결과는 candidate metadata다.
- 중요한 결론은 원문 path와 line range를 확인한 뒤 사용한다.
- semantic similarity만으로 사실, 원인, 성능 우열을 주장하지 않는다.
- 실제 동일 조건 측정 전에는 faster, better, optimal 같은 benchmark 주장을 하지 않는다.

## 5. 이주 규칙

- 원본 파일은 copy/reference 방식으로 보존한다.
- `*.sqlite`, `*.sqlite3`, `*.db` 및 기존 embedding index는 이주하지 않는다.
- 이주한 코드에는 원본 경로가 하드코딩되지 않도록 새 `config/profile.yaml`을 사용한다.
- 이주 후 원본과 새 프로젝트의 변경을 한 작업으로 기록하지 않는다.
