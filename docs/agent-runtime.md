# Provider-backed multi-agent runtime (internal prototype; unsupported)

> This document describes retained development code, not the supported 0.8.0
> public surface. The Codex-only preview does not register this MCP, publish its
> console entry point, or support Ollama/OpenAI/Anthropic/Moonshot execution.
> Codex owns model selection and native agent sessions. No compatibility or
> operational support is promised for the commands below.

## What this runtime is

The runtime turns the fixed eleven-role governance registry into independently
recorded provider sessions. Each activated role receives its own immutable task
packet, versioned role prompt, verified evidence bundle, dispatch reservation,
provider request, and validated decision. Independent workers may run in
parallel only after `scope_and_cost_governor` has reviewed the exact immutable
run plan.

No provider session may be created from a task packet alone. Every packet in a
run must carry the same `agent_creation_disclosure`, one common approval
reference, and the explicit `agent_creation` user opt-in. The disclosure binds
the reason for delegation, one task per requested agent, total agent count, the
direct-execution alternative, bounded additional token/time estimates, and
path/network/model/write scope. Preflight returns the full disclosure and its
hash so the user can review the explanation before approving anything.

One provider request is one plugin-owned agent session. Session directories,
request identifiers, prompts, evidence bundles, decisions, and failure records
are not shared between roles. This is real request and state isolation, but it
is not an operating-system sandbox and it does not create native Codex or
ChatGPT sidebar tasks.

## Unsupported host behavior

The plugin does not access a private Codex, ChatGPT, or Claude scheduler. It
does not bypass a subscription, entitlement, model policy, or provider bill.
Native host-agent dispatch remains host-owned. The existing Codex adapter can
export a hash-bound dispatch manifest for a host that implements that public
handoff.

The runtime also cannot convert a ChatGPT, Claude, Codex, OpenAI, or Anthropic
subscription into another product's API allowance. The user remains responsible
for the selected provider account, model entitlement, local server, credential,
rate limit, and bill. The controller only narrows how an already-authorized
provider can be used.

## Execution order

1. Validate the shared agent-creation disclosure, exact agent count, common
   approval reference, and explicit user opt-in. Missing or changed fields stop
   before any provider request.
2. Validate all task packets, expiry times, scopes, provider allowlists,
   failure policies, evidence references, and budgets.
3. Materialize an immutable run plan containing every task, prompt, evidence,
   route, model, cost estimate, and concurrency limit.
4. Present the full disclosure and exact plan/request hashes. A separately
   authorized, one-time grant must bind the disclosure hash.
5. Consume that grant before the first provider request is created.
6. Persist the governor session and reserve its dispatch before any provider
   request is sent.
7. Require the governor decision to name the exact reviewed run-plan hash.
8. Create a deterministic receipt bound to that plan and the governed task
   hashes.
9. Materialize one isolated session per worker and execute at most the approved
   parallelism.
10. Stop admitting new work after a blocking failure. Do not retry or switch
   providers automatically.
11. Preserve validated decisions and minimum failure tombstones in the runtime
   record. They do not become canonical research evidence automatically.

## Runtime records

Runtime state is separate from the canonical research ledger and derived
search indexes:

```text
data/governance/runs/<run-id>/
  run.json
  run-plan.json
  events.jsonl
  receipts/
  sessions/<session-id>/
    session.json
    task.json
    prompt-template.json
    evidence.json
    dispatch.json
    prompt.json
    raw-output.json (only when the resolved detail policy permits it)
    decision.json or failure.json
```

Artifacts are create-only. The event stream is append-only and hash-chained.
A dispatch reservation is flushed before the provider boundary. If a process
stops after reservation but before a result is recorded, the session is
`ambiguous` and is not automatically replayed.

Runtime records remain project-local, while one-time grants live in per-project
host state outside the project. They provide tamper evidence but are not an
external transparency log, proof of user presence, or an operating-system
security boundary. Protect both locations with host filesystem access controls.

Raw runtime prompts and provider outputs are not indexed as research memory.
Only a separate, user-authorized canonical research event may later make a
validated decision eligible for index refresh.

## Provider routes

The executable runtime supports exactly one explicitly selected generation
route per run:

- `loopback`: an OpenAI-compatible endpoint already managed by the user on a
  numeric loopback address and explicit port. The only accepted base URLs are
  exactly `http://127.0.0.1:PORT/v1` and `http://[::1]:PORT/v1`.
- `remote`: the configured OpenAI or Anthropic HTTPS REST adapter, fixed to the
  official service host for that provider.

The separate Codex integration can prepare a non-executing manifest for a
host-owned runner. `manifest` is not an `agent run` provider route and never
means that a GUI agent or provider request has started.

There is no automatic local-to-remote fallback. Loopback traffic is still a
network and model-execution capability, so task scope, approval, call/token
limits, timeout, and provider allowlist remain mandatory even when estimated
API cost is zero. Remote routes additionally require explicit current price
inputs and a cost ceiling.

Credential values are never accepted in task packets, MCP arguments, command
arguments, logs, or session artifacts. Configuration stores only an
environment-variable or keyring reference. A loopback server may omit
authentication.

The loopback transport rejects DNS names such as `localhost`, non-loopback
addresses, omitted ports, redirects, proxies, streaming responses, and paths
outside `/v1`. It sends one bounded JSON request with no retry or fallback.
Remote OpenAI and Anthropic adapters likewise do not cross-fallback after an
attempt; Anthropic is generation-only.

## CLI workflow

First configure exactly one secret-free generation reference. This writes a
model name and endpoint or credential reference, never a credential value.

```bash
# User-managed OpenAI-compatible local server; configuration does not contact it.
universal-research provider configure-loopback-generation \
  --root ./my-research \
  --endpoint http://127.0.0.1:11434/v1 \
  --model LOCAL_MODEL_NAME

# Or one official remote service. Use provider=openai or provider=anthropic.
universal-research provider configure-remote \
  --root ./my-research \
  --capability generation \
  --provider openai \
  --model REMOTE_MODEL_NAME \
  --credential-ref env:OPENAI_API_KEY
```

Create task packets that conform to `research-agent-task/1.0`, including one
`scope_and_cost_governor` packet, exact evidence boundaries, provider/network
scope, `approval_refs`, an `agent_creation` user opt-in, the same complete
`agent_creation_disclosure` on every packet, budgets, stop conditions, and a
fully resolved failure policy. Its `agent_count` includes the governor because
that role is also a provider session. Then preflight the exact route and budget.
Preflight constructs no provider request and displays the disclosure verbatim.

```bash
universal-research agent preflight packets.json \
  --root ./my-research \
  --route loopback \
  --approval-ref approval_001 \
  --max-workers 4 \
  --max-calls 11 \
  --max-input-tokens 200000 \
  --max-total-output-tokens 22000 \
  --max-output-tokens-per-agent 2000 \
  --max-cost-usd 0 \
  --input-cost-per-million-tokens-usd 0 \
  --output-cost-per-million-tokens-usd 0 \
  --timeout-seconds 60
```

Review the summary and copy its exact `run_plan_hash` and
`execution_request_hash`. The latter also binds the exact estimate snapshot and
provider configuration. Repeat the same packet, route, model configuration, and
budget in `agent approve`, adding both reviewed hashes and a timezone-qualified
future expiry. Approval is available only through the local CLI; there is
intentionally no MCP approval tool.

```bash
universal-research agent approve packets.json \
  --root ./my-research \
  --route loopback \
  --approval-ref approval_001 \
  --max-workers 4 \
  --max-calls 11 \
  --max-input-tokens 200000 \
  --max-total-output-tokens 22000 \
  --max-output-tokens-per-agent 2000 \
  --max-cost-usd 0 \
  --input-cost-per-million-tokens-usd 0 \
  --output-cost-per-million-tokens-usd 0 \
  --timeout-seconds 60 \
  --expected-run-plan-hash RUN_PLAN_HASH_FROM_PREFLIGHT \
  --expected-execution-request-hash EXECUTION_REQUEST_HASH_FROM_PREFLIGHT \
  --expires-at EXPIRY_ISO8601
```

This creates one create-only grant outside the research project, under
`${XDG_STATE_HOME:-~/.local/state}/universal-research-mcp/agent-approvals/`
and a directory named by the SHA-256 of the resolved project root. The state
root must be absolute and cannot be inside the project. The grant binds that
project-root hash, run ID, plan hash, estimate-snapshot hash, execution-request
hash, provider, model, network scope, provider-configuration hash, all budgets,
approval reference, authority source, and expiry. Run the unchanged plan with
an explicit CLI execution confirmation:

```bash
universal-research agent run packets.json \
  --root ./my-research \
  --route loopback \
  --approval-ref approval_001 \
  --max-workers 4 \
  --max-calls 11 \
  --max-input-tokens 200000 \
  --max-total-output-tokens 22000 \
  --max-output-tokens-per-agent 2000 \
  --max-cost-usd 0 \
  --input-cost-per-million-tokens-usd 0 \
  --output-cost-per-million-tokens-usd 0 \
  --timeout-seconds 60 \
  --execute-approved
```

The grant binds `agent_creation_disclosure_hash`, is consumed before the first
provider call, and cannot be reused,
including after a provider or process failure. Grant and consumption records
retain their content hashes and `authority_source`. A changed packet, estimate,
provider configuration, budget, route, expiry, execution request, project root,
or plan requires a new preflight and a new approval reference. The CLI and MCP
must resolve the same `XDG_STATE_HOME`. Inspect only concise inventory and
decision summaries.

The legacy `harness preflight` command remains available as a non-executing
compatibility diagnostic. `harness run` is fail-closed; it cannot bypass the
one-time grant and directs callers to this `agent` workflow.

```bash
universal-research agent status RUN_ID --root ./my-research
universal-research agent inspect RUN_ID --root ./my-research
universal-research agent inspect RUN_ID --agent-id AGENT_ID --root ./my-research
```

For a remote run, use `--route remote`, non-zero current input/output price
inputs, and a positive cost ceiling. The runtime never discovers prices or
selects a provider on the user's behalf.

## Execution MCP gates

The plugin also registers the separate
`universal-research-agent-runtime` MCP. Its preflight, status, and inspect tools
remain non-executing. Its run tool is disabled unless all three independent
conditions are true:

1. The server owner set `UNIVERSAL_RESEARCH_ENABLE_AGENT_EXECUTION=1` before
   starting the MCP.
2. That exact MCP run call contains `execution_approved=true`.
3. A matching, unexpired, unconsumed local grant was created by the CLI for the
   exact run plan and approval reference.

The third gate is consumed before provider dispatch. Merely installing the
plugin, starting the MCP, setting the environment variable, or putting an
`approval_ref` in a task packet is not sufficient. Use
`UNIVERSAL_RESEARCH_ROOT` to bind a plugin-started MCP to the intended project
root; otherwise it uses its current working directory. Approval state is never
accepted from inside that project.

This file-backed grant proves an exact, one-time state transition within the
configured host account; it is not cryptographic proof of fresh human presence.
A process running as the same OS principal with write access to the host-state
directory can create or replace data and recompute the unkeyed hashes. Preventing
that requires a separately privileged approval broker or an OS-backed signing
key with an interactive user-presence policy, which this package does not claim
to provide.

Accordingly, do not let an agent manufacture a disclosure and immediately run
the approval command as proof that the user agreed. The user must see and
approve the exact preflight disclosure through a host boundary that the agent
cannot silently satisfy. Without that boundary, keep execution blocked and do
the requested work directly.

## Failure and disclosure

The default failure policy is `blocking_only + ask + redacted`. A failure fact
and minimum tombstone are always retained; only detailed output retention is a
user choice. Validation, policy, evidence-integrity, or ledger failures block
the run regardless of a less strict step policy.

Chat output remains summary-only by default: state, outcome, material blocker,
usage estimate, and artifact paths. Source code, prompts, raw logs, provider
responses, and internal artifacts are shown only when the user asks for that
specific material.

Host visualization is a separate capability and remains off unless the task
scope and an explicit user opt-in both enable it. The agent runtime never turns
it on implicitly. Ordinary plotting permission does not enable a ChatGPT or
Claude Code Visualization Skill.
