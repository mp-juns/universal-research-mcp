# Local semantic retrieval

Universal Research supports lexical retrieval by default and optional offline
semantic/hybrid retrieval. The supported PyPI surface includes only:

- deterministic signed hashing for demos and lifecycle checks;
- an explicitly configured, already-present local SentenceTransformer snapshot.

Use `universal-research semantic models` to inspect the reviewed catalogue and
`semantic setup` to prepare a hash-bound environment plan. Package installation,
model download, GPU use, and execution require separate host approval. The
runtime never silently falls back to a remote embedding API.

## Pinned setup and cache verification

Starting in 0.8.4, `semantic setup` and `research_semantic_setup_plan` require an explicit full
40-character model commit SHA. `main`, tags and abbreviated hashes are rejected.
Choose a commit from the selected model's repository history; planning does not
contact the registry to resolve a moving reference. Hugging Face supports full
commit hashes as download revisions. See the [official download guide](https://huggingface.co/docs/huggingface_hub/en/guides/download#from-specific-version).

For example, replace the placeholder with the reviewed model's actual commit:

```bash
universal-research semantic setup --root /path/to/project \
  --model intfloat/multilingual-e5-base \
  --revision '<full-40-character-commit-sha>'
```

Review the returned plan before authorizing execution. Repeat the same arguments
with `--execute --confirm-plan-sha256 <displayed-plan-hash>` only after approving
the package installation and model download. Old v1 setup plans and any plans
whose paths, cache manifest or local existence state changed must be regenerated
and approved again.

Managed snapshots live under
`.universal-research/models/<repository-id-with-double-dashes>/<commit-sha>/`.
A fresh download uses a temporary sibling directory. Only a completed, nonempty
download with a new `.urmcp-model-snapshot.json` manifest becomes the final
snapshot. An interrupted download is not adopted as a cache. The manifest binds
the model ID, revision, relative file names, byte sizes and SHA-256 hashes. Only
the manifest itself and Hugging Face's `.cache/huggingface` bookkeeping are
excluded from the model-file inventory.

`--reuse-existing` permits reuse; it does not waive verification. An existing
snapshot needs a valid manifest for the requested repository and revision. The
manifest hash is included in the approved plan. Every recorded file is checked,
and missing, added or changed files, symlinks, hardlinks and reparse points are
rejected before package operations or configuration activation. Verification
never downloads a replacement, repairs the cache or overwrites its baseline.
The existing policy of installing the package extra still applies when reusing
an environment, so setup execution remains a network-capable approved operation.

Older unversioned setup caches are left untouched and are not silently migrated.
Use a new revision-specific snapshot through the approved setup flow. Inspect
an invalid existing snapshot before explicitly moving or removing it yourself;
do not blindly delete a setup lock. `.universal-research/.semantic-setup.lock`
serializes cooperating setup processes, and a leftover lock requires an
administrator to confirm that no setup is still active.

Managed semantic configuration records the manifest identity. A new resident
backend and the first encoder load both verify the snapshot locally. Its manifest
hash is also part of the semantic model/index key, so a different accepted
snapshot cannot reuse the previous model's vectors. A loaded encoder is reused
in memory; the runtime does not hash all model weights on every query. Restart
the process after intentional on-disk changes and approve a new setup as needed.

Existing v1 semantic configs and manually configured local paths remain
supported. `semantic configure --backend local` verifies and binds a manifest
when one exists; without a manifest it reports `unverified_manual_path`. This
manual compatibility path does not gain managed-snapshot reproducibility.

The initial manifest records bytes from the approved commit-pinned download;
it is not a separate publisher signature. These checks are not an OS sandbox
against an unrestricted process changing files during load or rewriting both
the configuration and its manifest. Semantic dependency versions are not fully
locked (`dependency_environment_locked` is false), and device/software changes
can still change numerical outputs. No model-quality or performance improvement
is implied by these integrity checks.

## Checkpoint verification and the loader generation

Transformers 5 builds a model on the meta device, installs the checkpoint
tensors, and only afterwards initializes whatever is still missing. Parameters
that came from the checkpoint are flagged so that final pass skips them, but the
flag only protects modules whose initializer either checks it or goes through
Transformers' patched `torch.nn.init` helpers. Several pinned remote-code
architectures instead mutate tensors directly (`module.weight.data.normal_()`,
`.zero_()`, `.fill_()`). Nothing can intercept those calls, so the finalization
pass overwrote every parameter with a fresh random distribution after a load
that reported no missing keys.

The result was a silent failure rather than a visible one. The load report was
accurate about loading and still ended with success, the vectors stayed
deterministic within one process, and index fingerprints matched, so repeating a
query in the same session reproduced the same wrong answer. Only comparing
against the checkpoint, or against another process, exposed it.

Two independent measures now apply, and both run on the ordinary load path:

- Weights loaded from the checkpoint are retained through the finalization pass.
  Parameters genuinely absent from the checkpoint are still initialized normally.
- Before first use, the loaded backbone is compared against the snapshot's own
  `.safetensors` tensors. A mismatch, an unverifiable parameter or a snapshot
  without a readable checkpoint raises instead of embedding. Verification costs
  roughly ten milliseconds against a one-to-two second load.

Embedding identity carries a loader generation (`!loader=<generation>`) next to
the snapshot hash, dtype and maximum length. It changes only when a loader
defect changed what the numbers mean. Because the identity is part of the index
key, indexes written by an affected release are reported `stale` and rebuilt
instead of being silently reused. See
[the 0.10.1 release notes](releases/v0.10.1.md) for the affected versions and
the reindexing procedure.

Both loaders share one implementation, in
`universal_research_mcp/runtime/encoder_loading.py`. The defect above existed
because the standalone builder in `tools/` carried a checkpoint workaround that
the supported runtime backend never received, so the two are now the same code:
the runtime backend, the CLI and the MCP server load through it, and the builder
adds only its model-card oracle on top.

Verification compares the backbone against the checkpoint; it is not a claim
about retrieval quality, and it does not certify that a model suits a corpus.

Semantic results remain candidates. A current semantic index does not replace
exact source fetch, SHA-256 verification, evidence eligibility, or semantic
relevance/conflict review.
