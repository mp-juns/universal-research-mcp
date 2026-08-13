# Canonical Input CLI Tutorial

The management CLI is an explicit administrative path. It writes only inside
the selected project root. The unified MCP also supports the narrower
prepare/commit flow described below; it cannot create approval records.

```bash
universal-research init ./my-research
mkdir -p ./my-research/docs
printf '# Note\n\nA verified observation.\n' > ./my-research/docs/note.md
universal-research source register docs/note.md --root ./my-research \
  --source-id src_note_v1 --source-type markdown
```

Read the returned `source_sha256`, then write an approval JSON object. The
approval scope must include the future record's study and kind.

```json
{
  "schema_version": "core/1.0",
  "record_id": "approval_note",
  "record_kind": "approval",
  "study_id": "study_demo",
  "occurred_at": "2026-08-12T10:00:00+00:00",
  "recorded_at": "2026-08-12T10:00:00+00:00",
  "status": "approved",
  "created_by": {"actor_id": "actor_owner", "actor_type": "human"},
  "payload": {"scope": {"study_ids": ["study_demo"], "record_kinds": ["observation"]}}
}
```

```bash
universal-research record approve approval.json --root ./my-research \
  --confirm approval_note
```

Create an observation JSON object with `approval_refs: ["approval_note"]` and
a `source_refs` entry whose `artifact_revision_id` ends in the registered hash,
for example `artifact_note@sha256:<source_sha256>`. Then validate and append:

```bash
universal-research record validate observation.json --root ./my-research
universal-research record append observation.json --root ./my-research \
  --approval-ref approval_note
```

`recorded_at` selects `data/events/daily/YYYY-MM-DD/events.jsonl`. Every
successful source or record append refreshes the lexical derived view. If the
refresh fails, canonical data remains appended and the command reports the
stale state plus `universal-research index ensure --kind lexical --root
./my-research` for recovery. `record template` prints a minimal valid core
record; it is a starting point, not an approval bypass.

After a current index is available, start the MCP and use
`memory_search_candidates`, then `memory_fetch_evidence` with the candidate's
event ID, line range, and SHA-256. A current content-hash mismatch withholds
content unless diagnostic opt-in is explicitly requested.

## Host-approved MCP ingestion

Use `research_prepare_ingest` to validate one non-approval Core record against
an existing human approval and to store a pending immutable draft. Include new
source registrations as `{path, source_id, source_type}` objects. Preparation
does not alter canonical JSONL.

Review the returned `draft_id`, `draft_sha256`, record ID, canonical-head hash,
and source count. Only then allow the mutating `research_commit_ingest` tool
with that exact pair. Commit rechecks all of those bindings, consumes the draft
once, appends the canonical record, and reports lexical/semantic refresh status.
It accepts neither a replacement record body nor a model-supplied approval flag.
