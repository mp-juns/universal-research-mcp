# Adversarial governance/ingest audit + installed-core verification (2026-08-29)

Preregistered (`benchmarks/protocols/adversarial-governance-ingest-v1.md`).
Deterministic, zero model tokens; the adversary is the harness.
Results JSON: `audit-results.json` (regenerate with
`python benchmarks/adversarial/audit_governance_ingest.py`).

## Governance + ingest fail-closed audit

| threat | fail-closed |
| --- | --- |
| T1 scope/privilege escape (delete, path escape, network, model-exec, hidden field, forged scope hash) | 6/6 |
| T2 cost-ceiling / plan evasion | 3/3 |
| T3 ingest receipt forgery | 4/4 |
| T4 one-time receipt replay | 1/1 |
| T5 draft tampering (wrong draft hash) | 2/2 |
| T6 approval bypass (missing / out-of-scope approval) | 2/2 |
| T7 authority poisoning (NaN/Inf, nested unknown, malformed packet) | 7/7 |
| **aggregate** | **25/25** |

Legitimate controls pass (a well-formed in-scope operation preflights; a
correctly-approved ingest commits exactly once), so the 25/25 is not
over-blocking. This closes the governance/ingest coverage gap the README
listed as unmeasured — though it measures the *controls'* fail-closed
behavior, not agent behavior under governance, which remains future work.

## Installed-core forward verification

The wheel built from this branch (sha256 `74d795b3…`, contains
`OMITTED-MISMATCHED-EVIDENCE`) was installed into the live environment
(`site-packages`, `universal-research`/`urmcp` on PATH). Verified against
the *installed* package, not the worktree:

- governance hash guards fire (3/3 NaN/Inf rejected);
- the v1.3 omission-enforcement source is present;
- **live stdio E2E**: `universal-research serve` (installed entry point) on
  a post-index-mutation fixture — `memory_fetch_evidence` reports
  `integrity_status: mismatched`, and a subsequent material
  `memory_check_evidence_eligibility` that omits the mismatched reference
  returns `status: blocked`, `block_reason: OMITTED-MISMATCHED-EVIDENCE`,
  `session_omitted_mismatched_fetches: 1`.

The plugin `.mcp.json` launches `universal-research serve`, so the next
Codex session uses this core without any plugin repack. The forward
guarantee is that the shipped binary enforces what the benchmarks
measured; a fresh model-in-the-loop benchmark under the installed core is
not part of this verification.
