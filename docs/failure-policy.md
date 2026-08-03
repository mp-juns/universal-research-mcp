# Governance v2 Failure Policy

Failure policy resolves independently for `stop`, `record`, and `detail` using
task packet, project profile, environment, then safe defaults. The accepted
values are `always | blocking_only | current_step`, `full | metadata_only |
ask`, and `full | redacted | hashes_only` respectively.

Every failure stops immediately before retry or downstream work. The stop mode
selects whether the whole workflow or only the current step remains halted.
Even `ask` creates an append-only minimum tombstone first; the user then chooses
the additional record detail. A suppressed or hashed detail never suppresses
failure identity, time, classification, blocking status, stop directive, or
the detail hash.

The policy record requests a graceful shutdown and host-owned timeout
escalation; it does not claim that either occurred. The executor must report the
actual shutdown result. In particular, the bounded provider harness cancels
only work that has not started and never claims authority to force-kill an
accepted remote request.

Environment keys are `URAG_FAILURE_STOP_POLICY`,
`URAG_FAILURE_RECORD_POLICY`, and
`URAG_FAILURE_DETAIL_LEVEL`. Secrets must not be put in policy values,
task packets, tombstones, or chat reports.
