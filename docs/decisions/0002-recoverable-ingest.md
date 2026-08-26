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

The administrator source/record CLI and MCP share one canonical writer lock
and the same before/after-bound atomic append implementation. The lock covers
ID/source/approval validation as well as replacement, preventing a CLI append
from racing an MCP replacement. Single-file CLI writes do not need MCP's
multi-file journal: an interrupted pre-rename write leaves the previous file
intact. No successful fsync/verification is inferred from merely submitting a
write. Recovery re-syncs already-applied operations before closing the journal.

Drafts, journals, consumption markers, locks, audit logs and canonical targets
all use the protected project file layer. Existing symlinks, Windows reparse
points and non-regular/multiply-linked target files are rejected. POSIX writes
use directory-relative no-follow operations. The portable fallback checks
parents before operations; it does not claim containment against a hostile
same-user process renaming directories concurrently. Windows file data is
flushed before replacement, but portable Python cannot provide POSIX directory
fsync parity there.
