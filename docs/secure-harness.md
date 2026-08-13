# Secure Codex/Docker Harness Preview

This preview keeps Codex authentication and orchestration on the host while all
approved test, build, and experiment recipes run in disposable Docker workers.
It is not a fork of Codex and it does not place Codex credentials in containers.

The host seals a content snapshot, exact image digest, typed operations, resource
limits, model, reasoning effort, and approval policy into one hashed plan. Codex
runs with user configuration ignored, its shell, web, apps, visualization, and
native multi-agent tools disabled, and only the closed worker MCP enabled.
Workers have no network, a read-only root filesystem, dropped capabilities,
bounded CPU, memory, PIDs and time, and no Docker socket. Project files are never
mounted directly. Proposed edits remain in quarantine until the user approves the
reported diff hash.

Start with `universal-research harness doctor`, then create a JSON plan with
`harness plan`, run `harness preflight`, and issue a one-time exact approval with
`harness approve`. `harness run` additionally requires `--execute-approved`.
Use `harness review` for claim eligibility, and `harness changes` plus `harness
apply --confirm-diff-hash ...` for explicit import.

The initial executable approval mode is `plan_once`. Network acquisition and GPU
execution fail closed unless separately represented in a future host-owned stage;
GPU requests already require exact UUIDs and experiment operations. Visualization
is disabled by default and is not exposed by this preview.
