# Legacy retrieval and native Linux migration

Search eligibility and evidence eligibility are separate. Canonical JSONL is
never rewritten or hash-backfilled during index rebuilding.

| Source reference | Candidate search | Evidence fetch / material claim gate |
| --- | --- | --- |
| Exact registered path and SHA | Allowed | Requires current bytes and exact event range to pass |
| Missing SHA (`legacy_candidate_only`) | Allowed | Blocked |
| SHA absent from registry (`unregistered_revision`) | Allowed | Blocked |

Both lexical and semantic search retain historical event summaries. Only exact
registered source revisions contribute source passages. `verified` in the source
classification means registry membership; it does not establish current file
integrity or scientific truth. Fetch rechecks current bytes and line ranges.

`urmcp doctor --root ROOT` reports index freshness separately from
`evidence_health`. `urmcp validate --root ROOT --verbose` lists ineligible event
IDs, paths, hashes, and reasons. `index ensure --kind lexical` still validates
registered source bytes, builds into a temporary database, checks integrity and
retrieval, and publishes only after successful verification.

For WSL migration:

1. Back up the durable store, including canonical records and derived indexes.
2. Preserve the canonical fingerprint. Do not replace historical event hashes
   with current file hashes. Historical restoration requires exact matching
   bytes and separately authorized append-only registration.
3. Restore the complete Hugging Face snapshot **and its referenced blob files**.
   Directory entries alone are insufficient: copied symlinks may be dangling.
   Restore locally cached custom model code when the model requires it.
4. Update the runtime model path. Preserve explicit `encoder_dtype` and
   `max_length` settings in `config/semantic.json`; these affect embedding
   identity and trigger rebuilding when changed.
5. Check CUDA from the host environment. A restricted shell may hide a GPU
   that remains accessible to the MCP service.
6. Rebuild lexical, then semantic; test candidate retrieval, an intact registered
   evidence fetch, and rejection of both ineligible reference classes.
7. Reinstall the Codex plugin. Review and trust its SessionStart hook using
   Codex `/hooks`: installation/enabling alone does not trust a hook.
   Confirm `hooks/list` reports the expected hook as `trusted`, then open a
   new task and check that it asks for scope before using task tools.
   Retrieval smoke tests do not verify host startup permission behavior.

For the recovered GTE revision `9bbca17d9273fd0d03d5725c7a4b0f6b45142062`,
the WSL environment used sentence-transformers 5.3.0, transformers 5.1.0,
tokenizers 0.22.2, float32, a 512-token limit, and 256 output dimensions.
Pin model-specific compatibility dependencies in the installation environment;
these are not universal requirements for every supported embedding model.
