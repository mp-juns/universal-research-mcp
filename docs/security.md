# URAG Security Boundary

The governance surface validates packets, manifests, decisions, and gates. It
does not approve commands, invoke models, or expose a generic filesystem tool.
The only canonical-write exception is the host-visible two-step ingestion
boundary: a noncanonical immutable draft is prepared first, then a mutating
commit tool rechecks its exact hash, canonical head, source hashes, pre-existing
human approval scope, and one-time consumption. Automatic startup writes remain
restricted to staged, verified project-local derived indexes.

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

Provider-backed runtime modules retained in the source tree are internal
prototypes. They are outside the 0.4.0 support and compatibility contract and
must not be treated as an enabled security boundary. A future provider release
requires a separate threat-model review, credential boundary, explicit
cost/network approval, and distribution contract before activation.

Every failure retains a minimum tombstone; policy controls only the additional
detail. Codex host visualization is off by default and ordinary data-plot
permission cannot enable it.
