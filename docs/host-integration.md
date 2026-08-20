# Host Integration Contract

> Support status for 0.7.0: Codex only. Other host adapters and
> local/remote model-provider routes are design work, not supported runtime
> integrations.

All hosts consume one role manifest and one task/decision contract. A host is a
facilitator, not a twelfth governance role: it may interpret a user request,
request role activation, render scoped instructions, and return a structured
decision. It cannot create a role, forge an approval, weaken a finding, or turn
an inconclusive result into a positive conclusion.

Codex integration uses the repository-local research-memory MCP and plugin
Skills. Its normal retrieval and governance tools are read-only. The narrow
`research_prepare_ingest` and `research_commit_ingest` tools are explicitly
marked mutating; compatible hosts must surface their normal
write-tool permission flow. `integrations.codex.adapter` renders a
validated packet plus a hash-bound scope-governor receipt into a host-owned dispatch request and refuses malformed output
without promoting it to research evidence. It intentionally cannot call Codex's
private scheduler; the current Codex host must dispatch the request under its
own entitlement. Critical-review batches share one snapshot but expose no first
verdict to another reviewer.

The server does not receive a portable cryptographic receipt of a Codex UI
approval, and must not claim it did. Instead, the separate local
`universal-research ingest approve` authority issues an HMAC-signed, expiring,
one-time receipt for one exact pending draft. The server verifies and consumes
that receipt together with the immutable draft SHA-256, unchanged
canonical/source state, and prior human approval record whose scope includes the
record. A write-ahead transaction may resume after partial filesystem failure
only with that same already-consumed receipt; this is recovery, not a second
approval or a replay with changed content. If a host is configured to auto-approve writes, that is the host
owner's policy choice and not a bypass the server can silently convert into
human authority.

## Codex subagent control

Universal Research keeps native multi-agent execution host-owned. The MCP can
read metadata-only status for descendants of the current root task and prepare
an immutable `disable`, `enable`, or `stop_active` proposal. It cannot apply the
proposal, target the root task, or target unrelated user tasks.

The separate trusted-host command applies an exact proposal hash. Disabling
uses Codex's own feature configuration command, writes a dedicated governed
profile with both multi-agent controls off, and requests `turn/interrupt` for
each active descendant returned by the app-server ancestor filter. Enabling
changes only the future-session policy and never creates an agent. Profile
changes require a new Codex session; current descendants are handled
separately through the interrupt protocol.

The host-control client follows Codex's WebSocket JSON-RPC transport. Set
`UNIVERSAL_RESEARCH_CODEX_APP_SERVER_URL` to a loopback-only `ws://` endpoint,
or `UNIVERSAL_RESEARCH_CODEX_APP_SERVER_SOCKET` to an enabled Unix-WebSocket
listener. Non-loopback URLs are rejected. The Codex Desktop-owned app-server
may expose a control socket while refusing additional clients; Universal
Research fails closed in that case instead of restarting or taking over the
desktop process. A separately started or daemon-managed app-server must expose
the control endpoint before status or interruption can run.

`stop_active` is a graceful `turn/interrupt`, not an operating-system process
kill. It lists only descendants bound to `CODEX_THREAD_ID`, displays details
only for currently active descendants, and never treats stored `notLoaded`
history as active work.

This is a convenience and audit boundary, not an administrator lock. A managed
deployment that must prevent users from re-enabling multi-agent functionality
should pin the feature off in Codex `requirements.toml`. A user opening a
separate top-level task is outside the descendant-control boundary.

Use the non-executing governance tools to prepare a dispatch manifest or the `urgov
dispatch` commands to print one. Manifest export does not write a project file
or start an agent. A host may preserve it as an internal audit artifact and
then execute only its explicitly scoped request.

Newly prepared dispatches are deep-copied from their task packet, sealed only
after the receipt binding is present, and revalidated against the build-time
hash immediately before export and again in the bounded execution harness.
Legacy unsealed manifests are rejected rather than silently upgraded. The
SHA-256 seal detects state drift; it is not a substitute for the host-owned
approval store or a cryptographic host signature.

A non-governor dispatch is mechanically blocked until a validated passing
`scope_and_cost_governor` decision has been converted into a receipt covering
the exact task-packet and scope hashes. The bounded parallel harness creates
and records this receipt after running the governor. Standalone hosts first use
`governance_prepare_scope_governor_receipt` or `urgov dispatch receipt`, then
pass the result to dispatch preparation. Every packet declares an estimated
cost, including an explicit zero for genuinely non-billable work.

Every operation proposed for execution first uses the closed declarative
preflight contract in `schemas/governance-operation.schema.json`. Unknown
fields, malformed typed fields, missing task/scope bindings, and out-of-scope
actions fail closed. An allowed gate result carries exact operation,
task-packet, and scope hashes so a host can pin and compare the same operation
immediately before its tool call. Generic `command`, `args`, and `env` payloads
are intentionally not accepted by this envelope.

The `operation-gate/3.0` result reports passing preflight only as
`preflight_passed`; the legacy `allowed`
field is intentionally absent so it cannot be mistaken for direct tool-call
authorization. `execution_authorized` remains false until the host has bound
and checked the action-specific arguments at its actual execution boundary.

This preflight hash does not by itself bind action-specific arguments such as a
search query or requested line range. A host that executes those arguments must
define a closed action-specific envelope, include it in the pinned operation,
and compare it at the actual tool boundary. The bundled provider runtime
revalidates its sealed runtime dispatch, but the MCP evaluator cannot intercept
arbitrary Codex tool calls and never executes them itself.

The retained development provider executor additionally requires a single-use
dispatch-artifact reservation issued by its owning `AgentRuntime`. The
reservation is atomically consumed before provider preflight or budget
reservation, so a fabricated direct call or replay fails before transport. This
is a same-process host trust boundary, not authentication against a malicious
host that controls the runtime itself.

For declared `benchmark` and `final_review` canonical outcomes, the promotion
boundary additionally requires a persisted secure-harness attestation binding
the project, workflow mode, sealed run plan, immutable result, and passed claim
review. An arbitrary Codex shell session cannot mint this attestation. It can
still create an ordinary un-attested local artifact; Universal must not present
that artifact as a governed benchmark or final-review result.

Host visualization remains a separate default-off capability; Codex model
entitlement does not grant visualization, filesystem, network, or spend
authority. Claude Code, OpenCode, OpenClaw, local model servers, and external
model APIs require future host/provider adapters and are not activated by this
contract.

## Verified technical names

A validated provider decision may not introduce a file, path, function, method,
class, module, or script as though it exists. Before the decision is promoted,
the runtime compares structurally identifiable technical references in its
output with the exact hydrated evidence bundle. An unverified reference blocks
the output; a model must instead say that no verified identifier is available.

This is a fail-closed guard for explicit technical syntax, not a claim that a
regular expression can understand every natural-language statement. Final
user-facing reports should cite the verified source path and line range, and
must not turn an unavailable identifier into a plausible-sounding name.
