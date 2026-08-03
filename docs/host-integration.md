# Host Integration Contract

> Support status for the 0.3.0 preview: Codex only. Other host adapters and
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

A non-governor dispatch is mechanically blocked until a validated passing
`scope_and_cost_governor` decision has been converted into a receipt covering
the exact task-packet and scope hashes. The bounded parallel harness creates
and records this receipt after running the governor. Standalone hosts first use
`governance_prepare_scope_governor_receipt` or `urgov dispatch receipt`, then
pass the result to dispatch preparation. Every packet declares an estimated
cost, including an explicit zero for genuinely non-billable work.

Host visualization remains a separate default-off capability; Codex model
entitlement does not grant visualization, filesystem, network, or spend
authority. Claude Code, OpenCode, OpenClaw, local model servers, and external
model APIs require future host/provider adapters and are not activated by this
contract.
