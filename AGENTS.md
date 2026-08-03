# Universal Research Memory MCP Agent Guide

이 폴더는 기존 연구 프로젝트와 분리된 범용 연구 MCP·Codex 플러그인 개발 공간이다.

## 절대 경계

1. 프로젝트 profile이 지정한 reference project는 read-only 참고 원본이다.
2. 원본 프로젝트의 결과·세션 로그·DB·embedding index에는 쓰지 않는다.
3. 원본 embedding DB는 schema와 adapter 설계 참고로만 읽는다.
4. 이 폴더의 event ledger, SQLite, embedding index, TODO, WORK_LOG는 원본과 독립적이다.
5. 기존 연구의 모델·수치·장치·benchmark 규칙을 범용 기능으로 일반화했다고 주장하지 않는다.

## 작업 규칙

- 실행·수정·복사 전에 계획, 명령, 파일, 성공 기준, 제외 범위를 기록한다.
- 사용자의 승인 범위를 넘는 명령, network, background, remote, package 설치, benchmark는 별도 확인한다.
- 원본과 새 프로젝트의 파일 출처를 attribution으로 구분한다.
- SQLite와 dense index는 canonical JSONL에서 재생성 가능한 derived view로 취급한다.
- 검색 결과는 후보이며, load-bearing conclusion은 원문 source와 line range를 확인한다.
- unexpected result가 나오면 즉시 중단하고 재계획한다.

상세 규칙은 [`agents/AGENT_RULES.md`](agents/AGENT_RULES.md)에 둔다.
