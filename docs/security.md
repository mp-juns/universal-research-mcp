# URAG Security Boundary

New sessions must ask for and wait on the human's session scope before task
execution. Defaults propose host-shell work, ordinary scoped file writes,
per-operation external network/download approval and zero agents. This is
delivered through MCP initialization instructions, plugin Skills and a
stateless SessionStart hook. It is **instruction-level guidance, not an
authenticated approval receipt or a global sandbox**. Hooks require separate
host trust. A profile, hook output or prior-session approval cannot confirm the
scope. See [session confirmation](host-integration.md#new-session-permission-confirmation).

The governance surface validates packets, manifests, decisions, and gates. It
does not approve commands, invoke models, or expose a generic filesystem tool.
The only canonical-write exception is the host-visible two-step ingestion
boundary: a noncanonical immutable draft is prepared first, then a separate
host-state authority issues a one-time HMAC-signed receipt. The mutating commit
tool rechecks the receipt, exact draft hash, canonical head, source hashes,
pre-existing human approval scope, and one-time consumption. It prepares a
write-ahead journal before mutation, verifies exact before/after hashes for
each canonical file, and marks the draft consumed only after all operations
verify. A partial failure is recoverable only with the same consumed receipt
and exact journal. The plugin explicitly disables automatic index writes at
startup with `serve --no-auto-index`. An explicitly approved `--auto-index` configuration remains limited
to staged, verified project-local derived indexes.

All ingest draft/journal/consumption/audit paths and the shared CLI/MCP canonical
lock reject symlink and reparse-point parents before creation. File operations
reject symlink, reparse-point, non-regular and multiply-linked final targets.
POSIX directory descriptors keep writes from following a replaced parent
symlink. The portable fallback checks every component but is not a sandbox
against unrestricted same-user filesystem mutation; directory fsync is not
portable to Windows. These protections do not broaden the human approval scope.

Event-bound evidence must match a complete registered path/hash/line-range
locator before fetching. Display context has separate bounds and never expands
the eligibility reference. Current-file bounds and the revision hash are both
required for eligibility. The two-evidence policy counts distinct event IDs;
it does not certify independent sources, observations or authors.

Source artifacts are untrusted content. Instructions embedded in research data
cannot expand role authority, request secrets, alter a verdict, or override the
task packet. Role manifests, task scope, and the controller's deterministic
checks take precedence.

Canonical records remain append-only. A task, decision, or role-manifest hash
mismatch fails closed. Derived index work may only occur after a recorded event
and must report retrieval verification; an index never outranks its source.

Telemetry, implicit model downloads, plaintext/chat/argv credentials, and
unapproved network execution remain disabled. The supported Codex plugin does
not register a provider execution MCP, expose a provider console entry point,
or call a local or external model service. Model selection, native agent
sessions, tool permissions, and product entitlement remain owned by Codex.

Universal-governed agent execution is default-deny unless every requested
agent shares one exact user-visible creation disclosure, a common approval
reference, and the explicit `agent_creation` opt-in. The disclosure covers the
reason, delegated tasks, count, direct alternative, token/time ranges, and
path/network/model/write scope. Provider-runtime and secure-harness grants bind
its hash and are consumed before the first provider request or Codex worker
process. This does not intercept native Codex subagent tools outside Universal's
execution paths. The plugin rule to ask first is instruction-level there; a
fresh secure-harness worker instead disables native multi-agent tools.

Neither a copied approval reference nor a same-user CLI invocation proves fresh
human presence. That proof requires a host approval UI, sandbox rule, separately
privileged broker, or OS-backed signing policy that the agent cannot satisfy on
its own. The package claims exact binding, ordering, and one-time consumption,
not universal control of an unrestricted host process.

The optional public demo transport is a separate, unauthenticated read-only
surface. It starts only from an explicit project root with a reviewed
`public-demo-manifest/1.0` document. That manifest enumerates and hashes the
complete canonical ledger, every registered source, the lexical projection,
and any semantic/profile configuration used by retrieval. A changed file,
changed file set, stale index, symlink, custom event/index path, startup index
write, legacy tool opt-in, or unallowlisted HTTP Host fails closed. The server
uses Streamable HTTP with DNS-rebinding protection and exposes a strict tool
allowlist; canonical ingest and pending-draft metadata are absent and retain a
second function-level denial.

Public demo mode is not an authentication or multi-tenancy boundary. It must
contain only data the operator has reviewed for unconditional public
disclosure, and should normally bind to loopback behind a TLS reverse proxy.
Private remote deployments require a separately reviewed identity provider,
tenant-isolated storage, authorization model, rate limit, and operational
secret boundary. The public demo currently rejects local learned embedding
models because their external snapshot is not yet included in the publication
manifest.

Provider-backed runtime prototypes were removed from the source tree
entirely (commit a40b5b2); no such module ships or remains. Any future
provider integration is outside the 0.9.3 support and compatibility contract and
must not be treated as an enabled security boundary. A future provider release
requires a separate threat-model review, credential boundary, explicit
cost/network approval, and distribution contract before activation.

Every failure retains a minimum tombstone; policy controls only the additional
detail. Codex host visualization is off by default and ordinary data-plot
permission cannot enable it.
