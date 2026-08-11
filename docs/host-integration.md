# Host Integration Contract

> Support status for the 0.3.1 preview: Codex only. Other host adapters and
> local/remote model-provider routes are design work, not supported runtime
> integrations.

All hosts consume one role manifest and one task/decision contract. A host is a
facilitator, not a twelfth governance role: it may interpret a user request,
request role activation, render scoped instructions, and return a structured
decision. It cannot create a role, forge an approval, weaken a finding, or turn
an inconclusive result into a positive conclusion.

Codex integration uses the repository-local read-only memory MCP and plugin
Skills. `integrations.codex.adapter` renders a
validated packet plus a hash-bound scope-governor receipt into a host-owned dispatch request and refuses malformed output
without promoting it to research evidence. It intentionally cannot call Codex's
private scheduler; the current Codex host must dispatch the request under its
own entitlement. Critical-review batches share one snapshot but expose no first
verdict to another reviewer.

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
