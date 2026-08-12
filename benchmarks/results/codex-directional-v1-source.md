# Synthetic provenance pilot source bundle

E01 | registry | Source `source-alpha` was registered by Mira on 2026-08-01 with SHA-256 `aaaa`.
E02 | registry | Source `source-beta` was registered by Theo on 2026-08-02 with SHA-256 `bbbb`.
E03 | evidence | The coral observation is supported by `source-alpha`, lines 3 through 5.
E04 | evidence | The amber observation is not supported by any registered source and must be reported as uncertain.
E05 | approval | Approval `approval-red` is a human approval for the scope `dataset:red` only.
E06 | approval | Approval `approval-blue` is a human approval for the scope `dataset:blue` only.
E07 | integrity | The indexed SHA-256 for `source-beta` is `bbbb`, while its current SHA-256 is `cccc`.
E08 | integrity | Mismatched source content is withheld unless diagnostic opt-in is explicitly requested.
E09 | negative-result | Trial N-7 found no supported causal link and remains a preserved negative result.
E10 | negative-result | Trial N-7 must not be rewritten as a positive finding in a later amendment.
E11 | attribution | Mira collected the source; Theo normalized the metadata; the external archive supplied the scan.
E12 | attribution | The assistant may summarize the material but must not be credited as a human collector.
E13 | amendment | Amendment A-2 supersedes the interpretation of A-1 but does not alter the original record.
E14 | amendment | Both A-1 and A-2 remain visible in the append-only ledger.
E15 | uncertainty | A result without verified source lines is an unresolved candidate, not confirmed evidence.
E16 | uncertainty | Reports must separate Observed facts from Interpretation and Uncertainty.
E17 | governance | A record outside its approval scope must be rejected before append.
E18 | governance | A duplicate record ID must be rejected even when its content differs.
