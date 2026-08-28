# 0004 — Content-anchored (passage-hash) evidence verification

Status: proposed (design + retrospective replay; not yet implemented in the
serving path). Grounded by `benchmarks/fault_taxonomy/results/REPORT.md`.

## Context

The fault-taxonomy census over a real eight-month corpus (686 evidence
links) showed the file-level hash gate is *directionally* right and
*range-level* imprecise:

- 61 file-hash alarms decompose as 32 slice-identical (C1), 27
  displaced-intact (C2m), 2 substantive (C3): **96.7% of alarms fire on
  citations whose cited content is still byte-identical** — the file merely
  grew or shifted around it.
- Line-number range hashing would rescue only the 32 C1 links; it still
  blocks the 27 displaced ones.

The store already extracts exactly the object we need: the derived index
builder (`_source_passage` in
`universal_research_mcp/tools/build_research_ledger_index.py`) resolves a
registered, line-addressable passage — (path, file sha256, line range,
content) — for every event it indexes. What it lacks is a *binding to the
passage content itself*: eligibility today compares only the whole-file
sha256.

## Decision

Bind evidence to the passage content, not the file snapshot.

1. **Registration.** `source_ref` gains `passage_sha256`: the SHA-256 of the
   exact byte content of the cited line slice at the registered revision
   (UTF-8, `\n`-joined, no normalization in v1). When no line range is
   registered, the passage is the whole file and `passage_sha256` equals the
   existing `source_sha256` (explicitly recorded, so whole-file citations
   remain distinguishable).
2. **Verification order** (eligibility check):
   a. current file slice at the registered range hashes to
      `passage_sha256` → **eligible, in place**;
   b. otherwise scan the current file for a line window whose hash equals
      `passage_sha256` → **eligible, displaced** (the response reports the
      new range so callers can re-register);
   c. otherwise → **ineligible: passage content changed or vanished** —
      the alarm that is actually worth stopping for.
3. **Fail-closed posture unchanged.** Links with no reference, no hash, an
   unrecoverable registered revision, or an invalid range keep their current
   (blocking) statuses; content anchoring never widens eligibility for
   evidence the chain cannot bind.

## Retrospective replay (measured, zero model tokens)

`benchmarks/fault_taxonomy/replay_content_anchor.py` re-verdicts the same
686-link population under these semantics, read-only
(`results/content-anchor-replay.json`):

| file-hash verdict | content-anchor verdict | n |
| --- | --- | ---: |
| hash_match 457 | pass (in place) | 457 |
| hash_mismatch 61 | pass (in place) | 32 |
| hash_mismatch | pass (displaced) | 27 |
| hash_mismatch | **alarm** | **2** |

The two remaining alarms are link-identical to the two C3 substantive
mismatches coded in Stage C. So on this corpus the design removes **59/61
(96.7%) of false alarms while preserving both true detections**, and flips
zero previously-passing links to alarms.

## What this does not fix (kept honest)

- **Broken chains (23.6%)**: 133 ref-without-hash + 20 no-ref links stay
  invisible; that is the source-reference backfill decision, not this one.
- **Blind spot (7.6%)**: partial-support claims over intact content pass
  either way; hash gates of any granularity cannot see claim overreach.
- **Activation**: whether an agent calls the gate before asserting is
  behavioral and measured separately (ablation/natural-session bench).
- Exact-byte matching is deliberate in v1; whitespace/normalization
  tolerance would trade auditability for recall and needs its own study.

## Implementation sketch

- Schema: additive, backward-compatible (`passage_sha256` optional; absent
  ⇒ current whole-file semantics). Migration can backfill it for every link
  whose registered revision is recoverable — the same `blob_at` recovery the
  census used succeeds for 518/686 links today.
- Serving path: the displaced scan is a rolling window over current file
  lines (replay cost: full-corpus re-verdict in seconds); per-call cost is
  one file read plus at most one linear scan.
