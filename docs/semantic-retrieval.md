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

Semantic results remain candidates. A current semantic index does not replace
exact source fetch, SHA-256 verification, evidence eligibility, or semantic
relevance/conflict review.
