# ADR-0002: Recoverable multi-file ingest

Status: accepted

One ingest may append source registrations and one event to different canonical
JSONL files. A filesystem cannot atomically rename both targets together, so the
system uses a write-ahead transaction journal rather than claiming impossible
cross-file atomicity.

The journal binds the draft, external receipt, each target, append payload, and
exact before/after hashes. Operations replace a fully fsynced same-directory
temporary file, are verified after rename, and are idempotent on recovery. The
external one-time receipt is consumed before canonical mutation, but its signed
consumption may resume only the same journal. The draft consumption marker is
written after every canonical operation reaches its expected after hash.

Partial failure therefore becomes `recovery_required`; it cannot silently
produce a successful response or authorize different content.
