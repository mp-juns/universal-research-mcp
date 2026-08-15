# Public demo deployment boundary

Public demo mode is an unauthenticated, read-only Streamable HTTP surface for a
corpus that the operator has already reviewed as public. It is not a shortcut
for exposing a private research workspace.

The publication manifest binds canonical ledgers, registered source files,
lexical index state, and any supported deterministic semantic index. Startup
fails when a bound file changes, an index is stale, a path is a symlink, or the
configuration requests a non-public capability.

The allowlist contains candidate search, latest-record lookup, exact evidence
fetch, evidence eligibility, ledger audit, and path-free publication status.
Canonical ingest, index refresh, model configuration, profile changes, agent
dispatch, and arbitrary file access are absent.

For a non-loopback deployment, explicitly configure every allowed Host and
Origin and place the process behind a separately reviewed TLS/rate-limiting
boundary. Private remote service additionally requires identity, authorization,
tenant-isolated storage, abuse controls, monitoring, backup, and incident
response. None of those controls are supplied by `--public-demo`.
