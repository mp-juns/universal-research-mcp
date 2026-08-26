# Secure Codex/Docker Harness Preview

This preview keeps Codex authentication and orchestration on the host while all
approved test, build, and experiment recipes run in disposable Docker workers.
It is not a fork of Codex and it does not place Codex credentials in containers.

The host seals a content snapshot, exact image digest, typed operations, resource
limits, model, reasoning effort, approval policy, and an agent-creation
disclosure into one hashed plan. The disclosure states why the worker is needed,
its exact delegated task, the direct-execution alternative, bounded additional
token/time estimates, and path/network/model/write scope. Codex
runs with user configuration ignored, its shell, web, apps, visualization, and
native multi-agent tools disabled, and only the closed worker MCP enabled.
Workers have no network, a read-only root filesystem, dropped capabilities,
bounded CPU, memory, PIDs and time, and no Docker socket. Project files are never
mounted directly. Proposed edits remain in quarantine until the user approves the
reported diff hash.

## Evidence hardening

Every `test` operation must declare exactly one `test_contracts` entry. Plan
creation checks the declared Python symbol, literal, assignment, or JSONL
key/value assumption against a bounded, symlink-free source file and seals the
source size and SHA-256 into the plan. Preflight and execution verify the same
contract again. Execution performs this check before consuming the one-time
approval, so source drift cannot spend the approval or start Codex.

Completed worker MCP calls are reduced to bounded, plan-bound tool receipts.
The runner rejects an unknown MCP server or tool, an operation/path outside the
sealed plan, ambiguous structured-result carriers, failed calls, malformed
result shapes, and missing receipts. Persisted run results are hash-checked and
revalidated with those receipts before review or attestation. Raw read content,
patch content, command output, and MCP arguments are represented by hashes and
bounded metadata rather than copied into the result record.

On POSIX hosts every bundled stdio MCP, including the isolated worker, uses
descriptor-backed asynchronous I/O with a 16 MiB inbound message ceiling,
avoiding executor-thread stdin stalls. Other hosts retain FastMCP's standard
stdio implementation, and non-stdio transports retain their SDK behavior. A
nonzero Codex process returns bounded, redacted diagnostics without exposing
stdout; a token-ceiling failure is persisted as an ineligible, plan-bound
failure record.

Start with `universal-research harness doctor`, then create a JSON plan with
`harness plan`, run `harness preflight`, and issue a one-time exact approval with
`harness approve`. `harness run` additionally requires `--execute-approved`.
Use `harness review` for claim eligibility, and `harness changes` plus `harness
apply --confirm-diff-hash ...` for explicit import.

`harness plan` fails when `agent_creation_disclosure` is absent or does not
exactly match the sealed operations. Review that full disclosure in preflight
before approving its plan hash. The one-time grant binds the disclosure hash and
is consumed before `CodexRunner.run` can create the worker process. The worker
then accepts only the already-consumed exact grant; it cannot consume approval
for itself. A changed disclosure or replay fails before process creation.

The local approval file is an exact host-state transition, not cryptographic
proof that a human was present. A shell-capable process running as the same OS
principal may be able to invoke the CLI unless the host permission UI, sandbox,
or a separately privileged broker prevents it. This harness makes ordering and
binding deterministic; the host remains responsible for the human-approval
boundary.

For a declared `benchmark` or `final_review`, set `workflow_mode` in the plan
and use `verification_mode: "strict"`. After a passed review, run `harness
attest` with the exact review hash. A canonical record whose payload declares
one of those modes must carry that exact persisted attestation; the normal
record and MCP ingest paths reject it otherwise. This does not prevent someone
from using a separate general Codex thread. It prevents that unbound work from
being promoted as an attested Universal benchmark or final-review result.

Use the normal host-state default consistently, or set
`UNIVERSAL_RESEARCH_HARNESS_STATE_ROOT` to the same absolute host-state path
for both `harness` and canonical ingest. Supplying `--state-root` for a run but
using a different state root during ingestion intentionally fails closed.

The initial executable approval mode is `plan_once`. Network acquisition and GPU
execution fail closed unless separately represented in a future host-owned stage;
GPU requests already require exact UUIDs and experiment operations. Visualization
is disabled by default and is not exposed by this preview.

## Limits

Tool receipts establish mechanical integrity and plan binding; they do not prove
that model-generated claims are semantically correct, that citations faithfully
support them, or that the harness reduces hallucinations across domains. Those
claims require separate, large-scale task and domain benchmarks. The preview also
does not yet specify or verify a complete production recovery state machine:
`plan_once` is the only executable approval mode, and only selected failure
classes have persisted recovery artifacts.
