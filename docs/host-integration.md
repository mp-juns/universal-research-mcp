# Host Integration Contract

> Support status for 0.8.3: Codex only. Other host adapters and
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

Universal-governed execution has a default-deny agent-creation contract. Before
a dispatch, provider session, or secure-harness worker is eligible, the host
must show one exact disclosure containing the delegation reason, bounded task
per agent, count, direct-execution alternative, minimum/likely/maximum
additional tokens and elapsed minutes, and exact path/network/model/write
scope. The task packets must carry that unchanged disclosure, a common approval
reference, and an `agent_creation` user opt-in. The disclosure hash is part of
the task scope, dispatch, run plan, grant, and consumption receipt.

The provider runtime and secure harness consume an external one-time grant
before the first provider request or Codex worker process. Missing approval,
changed disclosure, count drift, replay, or approval consumed after launch all
fail closed. Dispatch-manifest generation remains non-executing and marks host
approval as required; a host must not interpret a valid manifest as permission
to spawn.

Universal Research keeps native multi-agent execution host-owned. In the
currently tested Codex 0.147.0 contract, a local stdio MCP server receives
neither an authoritative per-call root-thread identity nor a proposal-bound,
single-use user approval receipt. `CODEX_THREAD_ID` is caller-inherited process
environment rather than authenticated per-call identity, and repeating a
proposal SHA-256 proves knowledge rather than human authorization. Loopback
locality alone does not bind an operation to the current task or to human
approval. A carefully owned Unix socket can constrain the OS principal, but it
still needs authenticated, operation-bound task context and approval.

The public surface therefore fails closed:

- `codex_host_agent_status` returns structured `unavailable` and no thread data;
- `codex_prepare_agent_control` returns `unavailable` and creates no proposal;
- `universal-research codex-agents` exposes `status` only;
- direct Python `apply_codex_agent_control` raises before filesystem, process,
  configuration, or App Server effects.

This intentionally removes the earlier hash-confirmed apply path. It must not be
restored by adding a TTY prompt, environment flag, second hash argument, or a
same-user helper process. Those mechanisms remain callable by a shell-capable
agent and are not independent authority.

This does not add an MCP interceptor around Codex's native `spawn_agent` tool.
Official Codex documentation states that current releases can delegate after a
direct request or applicable project/Skill instruction, that child agents
inherit the parent permission mode, and that `agents.enabled=false` disables
multi-agent tools. Therefore the plugin can require the explanation/approval
exchange in its governed Skill and can launch fresh secure workers with agent
tools disabled, but it cannot retroactively block a native spawn performed in a
different or unrestricted host path. See the official
[Codex subagents documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents).

For a fresh isolated Codex worker, the secure harness explicitly applies strict
configuration with the documented selectors plus a Codex-0.147.0-observed
defense-in-depth selector off:

```text
agents.enabled=false
features.multi_agent=false
features.multi_agent_v2=false  # observed in the tested 0.147.0 build
```

These are startup settings. Editing a profile or feature file does not revoke
tools already loaded into the current task, and an interrupt does not prevent
the root task from spawning again. A deployment may additionally pin supported
`features.multi_agent=false` in admin-enforced `requirements.toml`, but only a
fresh-session probe that rejects spawning establishes the effective result for
that runtime. Strict parsing catches misspelled known settings; it cannot prove
that an unknown future backend is disabled. Such a runtime remains unverified
until a capability/spawn probe passes.

A future protected broker must receive authenticated, operation-bound task
context from the owning host integration rather than infer identity from an App
Server connection, issue its own expiring single-use receipt bound to the exact
proposal, keep authority and consumption state outside the agent workspace,
pin and authenticate its Codex executable/socket, and maintain a durable
resumable journal for profile, feature, and per-turn interrupt effects.
Until that boundary exists, Universal Research makes no claim that it can
disable or manage current Codex agents. A separate top-level task and a
same-user process with unrestricted host access remain outside MCP control.
The secure harness also assumes its host launch environment and the `codex`
executable resolved from `PATH` are trusted; command construction alone is not
an adversarial executable-authentication boundary.

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
