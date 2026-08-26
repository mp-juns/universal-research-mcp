SESSION SCOPE CONFIRMATION — ASK FIRST

At the start of EVERY NEW Codex session in which Universal Research MCP is
loaded, your first user-facing response must ask the user to confirm the
session's permission scope and wait for their explicit reply. Present the
question even when the initial task sounds actionable. The default proposal
is not approval. Before that reply, do not run host shell commands, research
tools, file operations, network requests, downloads, models or agents.
Reading the host-provided startup instructions is not task execution.

Propose these defaults in the user's language and name the current workspace:
- Execution: Codex host shell, within the host's existing sandbox and policy.
- Files: ordinary creation and editing inside the confirmed workspace/task
  scope are allowed after session confirmation; no blanket outside-root access.
- External network: ask for explicit user approval BEFORE EACH operation,
  including web tools, connectors, remote commands and API requests.
- Downloads/installations: ask BEFORE EACH operation; disclose the source,
  destination, purpose and material size/cost. Network approval alone is not
  permission to install or execute downloaded content.
- Agents: create ZERO agents by default. No native subagents, separate model
  sessions, provider workers or delegation without a separate exact disclosure
  and explicit user approval. Do not enable agent features yourself.

A Korean opening question is:
"이 세션은 현재 작업 폴더에서 호스트 셸을 사용하고, 작업 범위 내 파일 생성·수정은
허용하며, 외부 네트워크·다운로드는 매번 사전 승인, 에이전트 생성은 없음으로
시작할까요? 변경할 범위나 추가로 허용할 경로가 있으면 알려주세요."

After the user's reply, briefly restate the confirmed scope. Keep it in this
session's conversation only. Never create an approval receipt from this text,
a profile, a local flag, a prior session, silence or an assistant summary.
On resume/compaction of the SAME session, preserve an explicit in-session user
confirmation; if it is absent, uncertain, or the workspace/scope changed, ask
again before task execution. A new session always requires a fresh question.

Ordinary file permission does not authorize deletion, destructive operations,
reference-project writes, canonical research ingestion, model/benchmark runs,
background jobs or remote publication. Preserve their existing specific gates.
Agent approval still needs reason, tasks/count, direct alternative, token/time
ranges and exact scope; a generic "go ahead" is not that approval.
Never use a remembered command allowlist, persistent prefix approval, automatic
approval reviewer or sandbox escape as a substitute for the required explicit
per-operation network/download approval.

This is host-facing workflow guidance, NOT an OS sandbox, a cryptographic
approval, or proof of host-wide enforcement. Follow stronger host/admin policy.
MCP initialization and plugin hooks cannot grant host permissions or revoke
tools from an already-running session. A read-only/public server stays read-only.
If the host cannot enforce a requested restriction, report that limitation
and stop the affected operation rather than claiming the restriction is active.
