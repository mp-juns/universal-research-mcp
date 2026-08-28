# Architecture deep dive — what this system is, and where each claim lives in code

Every structural claim on this page cites a file and line in this
repository. Nothing here is aspirational: if a line number is given, the
identifier exists there. (Line numbers drift with edits; each citation
names the identifier so `grep` re-finds it.)

## 1. The goal

The project exists to solve one failure class: **an agent asserting a
recorded research value whose evidentiary basis no longer holds** — the
file drifted, the claim was withdrawn, the source was never registered.
The contract is stated in [README](../README.md) and enforced in code:

> search returns candidates; verification establishes source integrity;
> the host still reviews relevance, conflicts, and the final claim.

The design target is not "make the model honest" but **make the evidence
chain mechanically checkable and fail-closed**, then measure which parts
of safety actually come from the mechanism (see the benchmark section of
the README — the measured answer is: enforcement is real, adoption and
citation discipline are separate problems).

## 2. Canonical authority: the append-only ledger

The single source of truth is a set of append-only JSONL files:

```
data/events/sources.jsonl        # source registry: path → SHA-256 at registration
data/events/daily/<date>/events.jsonl   # canonical events, append-only
```

- `universal_research_mcp/server.py:53` — `EVENTS_ROOT` defaults to
  `ROOT / "data/events"`.
- `universal_research_mcp/indexing/lexical.py:111` —
  `_canonical_paths()` defines exactly this file set
  (`sources.jsonl` + `daily/*/events.jsonl`) as canonical.
- `docs/architecture.md:30-32` — everything else (Markdown notes, work
  logs, session files) is a *display adapter*, "not canonical authority."

A canonical event may carry `source_refs`: path, line range, and the
registered SHA-256 (`artifact_revision_id: "...@sha256:<hash>"`). That
triple is the evidence unit the whole system verifies.

Writes are governed twice over. Human-side: `record approve` /
`record append` require a pre-existing approval record whose scope covers
the record's study and kind. Model-side ingestion is two-step and
receipt-bound (§5).

## 3. The RAG: derived, rebuildable, never authoritative

Indexes are **derived views** of the ledger — deletable and rebuildable at
any time, and never written by retrieval.

**Lexical (SQLite FTS5).**
- `universal_research_mcp/tools/build_research_ledger_index.py:142` —
  `CREATE VIRTUAL TABLE source_passage_fts USING fts5(...)` — passage
  index; `:152` — `event_fts` — event-summary index.
- `universal_research_mcp/tools/build_research_ledger_index.py:172` —
  `_source_passage()` derives a passage only from "a source path that
  both the event and canonical source registry bind to the same SHA-256",
  keeping "the event's already-authorized line range" (docstring
  `:177-181`). It re-hashes the file live and raises on mismatch
  (`:203`). **The index never crawls arbitrary project files.**
- `universal_research_mcp/indexing/lexical.py:593` — an index build
  first runs `validate_registered_sources(...)`: a drifted registered
  source refuses the rebuild (fail-closed reindexing).

**Semantic (optional, offline).**
- `universal_research_mcp/indexing/semantic.py:42` — tables
  `metadata`, `embeddings`, `passage_embeddings`.
- `universal_research_mcp/indexing/semantic.py:262` — the semantic view
  is built *from the lexical index* (`FROM source_passage_fts`, `:295`),
  not from raw files — so both retrieval modes inherit the same
  registry-bound passage discipline.
- No network: `universal-research semantic setup` plans an isolated
  local SentenceTransformer environment; without a configured model the
  server discloses `routing.semantic_backend` and hashing-demo scores
  are never presented as model similarity (`server.py`, INSTRUCTIONS).

## 4. RAG ↔ MCP: the three-call evidence chain

The MCP server (`universal_research_mcp/server.py:156` —
`DescriptorBackedStdioFastMCP("Universal Research", instructions=INSTRUCTIONS)`)
exposes **30 tools** (count: `grep -c "@mcp.tool" server.py`). Every index
read is physically read-only: `open_readonly()` opens sqlite with
`mode=ro` + `PRAGMA query_only = ON` (`server.py:281-286`).

The chain a model is expected to run:

1. **`memory_search_candidates`** (`server.py:1009`) — BM25 over
   `source_passage_fts` joined to events (`:447-452`). The envelope is
   stamped `"candidate_only": true` (`:1084`); the shipped INSTRUCTIONS
   repeat it: *"a search score is not evidence"* (`:83-84`).
2. **`memory_fetch_evidence`** (`server.py:1101`) — refuses unregistered
   paths (`:1129`) and unregistered hashes (`:1133`); reads the file
   once into a snapshot so content and hash describe the same bytes
   (`:1151`); returns
   `integrity_status: "matched" | "mismatched"` (`:1152-1153`) and, on
   mismatch, withholds content and returns `mismatch_guidance` pointing
   at events bound to the *current* revision instead.
3. **`memory_check_evidence_eligibility`** (`server.py:1398`) — the
   gate. `claim_type` is required with no default (`:1411-1414`): the
   caller must classify the claim (`result`, `comparative`, `causal`,
   `release` auto-materialize; two-source rule for
   comparative/causal/release — `core/claim_gate.py:14-20`). Each cited
   reference is re-fetched against its registered event and hash; the
   receipt contains no source content (`:1345-1351`).

## 5. Control: every trust boundary is enforced, and where it isn't, it says so

**Index staleness gate.** `server.py:321` —
`_require_current_lexical_index()` blocks search/fetch when the derived
index is not current, with an explicit remedy string (`:344-348`). Its
verdict cache documents its own evasion window (stat-identity spoofing)
and the env var that disables the cache (`:330-332`) — a disclosed
limitation, not a silent one.

**Two-step, receipt-bound ingestion.**
- `server.py:1460` `research_prepare_ingest` → immutable pending draft,
  never a canonical record.
- `server.py:1483` `research_commit_ingest` → requires the exact
  `draft_sha256` and a **one-time HMAC receipt** issued outside the MCP:
  `runtime/ingest_approval.py:1-4` — "The receipt authority lives
  outside a research project and is never exposed as an MCP tool";
  signature `hmac.new(key, canonical, sha256)` (`:238-240`), verified
  with `compare_digest` (`:313`), consumed atomically before append
  (`consume()`, `:151`; replay → "receipt was already consumed",
  `:179`).
- Adversarially measured: forgery, replay, tampering, approval bypass —
  25/25 hostile inputs fail closed
  ([audit](../benchmarks/adversarial/audit-results-README.md)).

**Session scope (honestly labeled).**
`session_scope.py:3` — `SESSION_SCOPE_INSTRUCTIONS` mandates ASK-FIRST at
every new session and ZERO agents by default — and self-discloses that it
is "host-facing workflow guidance, NOT an OS sandbox" (`:48-51`). The
ablation benchmarks measured exactly this boundary: instructions are
delivered but ignored until workspace policy points at the server.

**Citation-discipline enforcement (measured into existence).**
- `server.py:1295` — `_SESSION_FETCH_LOG` records every fetch this
  session made.
- `server.py:1304` — `_omitted_mismatched_fetches()` finds references
  the session fetched, saw fail integrity, and did not cite.
- `server.py:1388` — an otherwise-eligible **material** claim is flipped
  to `blocked: OMITTED-MISMATCHED-EVIDENCE` with a remedy; routine
  claims get disclosure only. The code comment cites the measurement
  that forced this design: disclosure alone was overridden in 9/13
  surfaced cases
  ([v1.2/v1.3 results](../benchmarks/results/rebench-v1.2-v1.3-citation-discipline-20260829.md)).

**Governance layer (deterministic, no inference inside).**
- `governance/registry.py:57-62` — the role roster is fixed:
  "role registry must contain exactly the fixed eleven-role roster"
  (`GOV-REGISTRY-002`), bound by `registry_hash`. The eleven:
  scope_and_cost_governor, retrieval_governor,
  research_memory_maintainer, substance_reviewer,
  reproducibility_reviewer, analysis_objectivity_auditor,
  benchmark_control_auditor, cold_adversarial_reviewer,
  correction_executor, paper_evidence_evaluator,
  user_alignment_reviewer.
- `governance/scope_policy.py:479` — `operation_gate()` "never executes
  the operation": it preflights one operation against a validated task
  packet, fails closed on unhashable input, and always returns
  `execution_authorized: false` — authority to execute stays with the
  host and human.

## 6. Honest boundaries (also in code or measurement)

- `_require_private_write_surface` (`server.py:219-221`) only guards
  public-demo mode; approval authority lives in the receipt store, not
  in that function.
- Two passage-derivation implementations exist (standalone tool
  `build_research_ledger_index.py:172` and packaged
  `indexing/semantic.py:188`); they enforce the same registry binding
  but are separate code paths.
- The integrity gate does not judge semantics: withdrawn, stale-but-
  hash-valid, and irrelevant evidence defeat every measured arm — by
  design and by measurement (negative controls in the benchmarks).
- Model-in-the-loop behavior under the governance contracts is
  unmeasured; the 25/25 audit covers the controls' fail-closed behavior
  under direct hostile input only.
