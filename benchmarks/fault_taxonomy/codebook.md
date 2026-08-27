# Fault Taxonomy Codebook v1

Single most severe code per link, one-line justification citing the exact
lines used. Rendered as yes/no questions per `cit_ais2023_binary_decomposition`
(binary decomposition raises agreement); finer codes lower agreement
(`cit_ragtruth2024_agreement_adjudication`: 91.8%→78.8% response→span), so
never assign more than one code. Examples quoting the private corpus live in
the local annex, not in this public file.

## Stage B tree (hash_match links) — answer in order, stop at first "no"

1. Does the registered range talk about the same subject/entity/metric as
   the record's claim? — no → **B3 irrelevant**
2. Read only the registered range. Does it state the claim at the claimed
   strength (same value, same scope, same direction)? — no, weaker/partial
   → **B2 overreach**
3. Run the fixed conflict probe: one lexical top-10 store query built from
   the claim's key terms. Does any *other* registered source in those
   results contradict the claim? — yes → **B4 conflict** (name the source)
4. Does any later record or source in those results supersede or withdraw
   this claim while the range itself is unchanged? — yes → **B5 superseded**
5. Independent of later material: was the range's content already wrong at
   registration time? (You must cite what establishes this.) — yes →
   **B6 wrong at registration**
6. Otherwise → **B1 supports**

Severity order (most severe first): B6 > B4 > B5 > B3 > B2 > B1.

## Stage C tree (hash_mismatch links, after machine triage)

0. Machine label `C1_candidate` (diff ∩ cited range = ∅): confirm on the
   seeded subsample only; confirmed → **C1**.
1. File deleted or moved? → **C4**
2. Registered revision unrecoverable (no diff possible)? →
   **C-indeterminate** (never force C1–C3)
3. Diff touches the cited range. Read both revisions of the range. Does the
   current range still support the claim at the same strength? — yes →
   **C2** (wording/format) · no → **C3** (true positive)

## Recording

Per link: code, one-line justification, lines cited, (B4/B5) conflicting
source path, (C) diff summary. Coder 2 sees this file only.
