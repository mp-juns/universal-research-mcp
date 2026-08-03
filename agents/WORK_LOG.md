# WORK_LOG

## 2026-08-03 — universal research MCP bootstrap

### Ownership

- Requested by: User
- Idea proposed by: User
- Decision made by: User
- Planned by: Codex
- Executed by: Codex
- Verified by: Pending user-requested migration handoff; no tests run
- Approval evidence: User explicitly requested `이주시작해 README에 뭘할건지 정의하고`

### Attribution

- User contribution: Defined the separation boundary, read-only reference policy, and goal of generalizing the MCP/plugin.
- Codex contribution: Created the independent project scaffold, README definition, generic operating documents, and migration plan.
- External contribution: None used.
- Existing work preserved: Original project and its research DBs remain untouched.
- Not performed by Codex: No experiment, benchmark, network, background watcher, remote task, or source-project mutation.

### Initial commands

```text
mkdir -p <workspace-root>
python3 <codex-skill-root>/plugin-creator/scripts/create_basic_plugin.py universal-research-memory --path <workspace-root>/plugin --with-mcp --with-skills --with-scripts
mkdir -p agents db/schema db/reference mcp/project_search scripts requirements tests data/events data/index .codex-plugin
mkdir -p config
```

### Results

- Created `<workspace-root>`.
- Created plugin scaffold at `plugin/universal-research-memory`.
- Added independent README, AGENTS, agent rules, TODO, and WORK_LOG.
- Source-code and schema-copy phase is pending in this task.

### Migration execution

The first relative-path copy attempt failed because the command ran with the new project as `cwd` while referring to `tools/project_search/...` as though it were still in the source project. Exit output was `cp: cannot stat ...`; no source file was modified and no intended source file was copied.

The corrected foreground copy used absolute source paths and copied:

- `tools/project_search/*.py`, JSON/config files, requirements, and static placeholder into `mcp/project_search/`
- research ledger, semantic index, watcher, device, correction, and reference-corpus scripts into `scripts/`
- project-search and research-memory requirements into `requirements/`
- focused MCP/index tests into `tests/`

The copied MCP defaults were then changed to use this project's `data/index/` paths. The plugin manifest and `.mcp.json` were configured for the independent MCP path. Existing SQLite files and embedding artifacts were not copied.

### Plugin validation

- First validation attempt: failed before process creation because the supplied `cwd` did not exist; no project file was affected.
- Correct command: `python3 <codex-skill-root>/plugin-creator/scripts/validate_plugin.py <workspace-root>/plugin/universal-research-memory`
- Result: `Plugin validation passed`.

## 2026-08-04 — generic runtime adapter separation

### Ownership

- Requested by: User
- Idea proposed by: Codex in the previous handoff
- Decision made by: User
- Planned by: Codex
- Executed by: Codex
- Verified by: Pending source-level handoff; no server/test execution planned
- Approval evidence: User replied `ㅇㅇ ㄱㄱ` to the generic adapter next step.

### Attribution

- User contribution: Approved continuing the independent generic MCP/plugin project.
- Codex contribution: Planned the adapter boundary and will implement the new runtime configuration layer.
- External contribution: None.
- Existing work preserved: Original research project, its DBs, and its logs remain untouched.
- Not performed by Codex: No index rebuild, event append, server run, test, benchmark, network, background, or remote work.

### Planned mutation

- Add `mcp/project_search/runtime_config.py`.
- Change `mcp/project_search/server.py` and `mcp/project_search/mcp_server.py` to consume generic runtime settings.
- Update `config/profile.yaml` and README adapter documentation.

## 2026-08-03 — universal research framework implementation

### Ownership

- Requested by: User
- Idea proposed by: User
- Decision made by: User
- Planned by: Codex
- Executed by: Codex
- Verified by: Codex through fixture and compatibility tests; user review pending
- Approval evidence: User explicitly approved implementation with `ㄱㄱ 구현해` and remaining scope with `ㅇㅇ 이제 다하십쇼 나머지`.

### Scope and exclusions

- Implemented only in this independent project: core schemas, ledger validation,
  amendment/proposal/audit/view adapters, local read-only MCP, plugin Skill,
  fixtures, tests, and documentation.
- Did not access or modify the reference project, historical result/log/DB,
  existing indexes, model artifacts, remote services, or marketplaces.
- Did not install dependencies, create a derived index, load an embedding model,
  or start a daemon.

### Results

- Added versioned Core, Pack, and Project Profile schema contracts.
- Added a dependency-free validator that supports current legacy event records.
- Added narrow, fail-closed resolved amendment views that never modify canonical
  input and an approval-gated append helper not exposed through MCP.
- Added read-only audit findings and Markdown plan/work-log projections.
- Made lexical and semantic index builders reject invalid canonical input before
  creating a derived index.
- Replaced the plugin's daemon proxy configuration with a local, read-only
  lexical/evidence/audit MCP; semantic retrieval remains an optional derived
  adapter and not evidence.

### Verification commands and outcomes

- `python3 -m unittest discover -s tests -p 'test_core_ledger.py'`: 4 passed.
- `python3 -m unittest discover -s tests -p 'test_research_memory_mcp.py'`: 2 passed.
- `python3 -m unittest discover -s tests -p 'test_contract_files.py'`: 2 passed.
- `python3 -m unittest discover -s tests -p 'test_framework_operations.py'`: 4 passed.
- `python3 -m pytest -q tests/test_build_research_ledger_index.py`: 4 passed.
- `python3 -m pytest -q tests/test_build_research_semantic_index.py`: 15 passed.
- `python3 <codex-skill-root>/skill-creator/scripts/quick_validate.py plugin/universal-research-memory/skills/research-workflow`: passed.
- `python3 <codex-skill-root>/plugin-creator/scripts/validate_plugin.py plugin/universal-research-memory`: passed.
- Applied the local plugin cachebuster `0.1.0+codex.20260803135458` and reran
  plugin validation successfully. No marketplace entry was created or changed.

### MCP runtime verification

- User explicitly approved installing the documented MCP runtime for transport
  verification. Installed `mcp[cli]>=1.27,<2`, resolving to `mcp 1.29.0`.
- Loaded `mcp/research_memory/mcp_server.py` without starting a server or
  touching a ledger/index. Registered tools: `memory_search_candidates`,
  `memory_latest`, `memory_fetch_evidence`, `memory_audit_ledger`, and the
  compatibility aliases `research_search`, `research_latest`, `research_fetch`.
- No marketplace entry, reference-project file, historical DB/log, derived
  index, model, or remote research service was changed.

## 2026-08-04 — fixture E2E and local marketplace installation

### Ownership

- Requested by: User
- Planned and executed by: Codex
- Verified by: Codex; GitHub publishing remains blocked by missing repository
  and expired GitHub CLI authentication.

### E2E verification

- Added an isolated temporary fixture that creates its own canonical JSONL and
  derived lexical SQLite index, then verifies MCP candidate search, source-line
  fetch, and read-only audit. No project ledger or index was created.
- Closed SQLite connections explicitly in lexical and semantic index builders
  after an E2E `ResourceWarning` exposed the previous lifecycle gap.
- Checks passed: E2E unittest, lexical builder pytest (4), and semantic builder
  pytest (15).

### Plugin marketplace

- The workspace top-level `.agents` directory is read-only, so the supported
  marketplace layout lives under `marketplace_root/.agents/plugins/`.
- Registered marketplace `universal-research-local` with Codex and installed
  `universal-research-memory` version `0.1.0+codex.20260803135458`.
- Removed the temporary incorrect `marketplace/` directory created during the
  failed manifest-layout attempt; it contained only the generated manifest and
  symlink, no research data.
- `codex plugin list` confirms the plugin is installed and enabled.

### GitHub blocker

- `<workspace-root>` was not a Git repository and had
  no remote.
- `gh auth status` reports the active GitHub token is invalid. No GitHub or
  remote change was attempted.

## 2026-08-04 — public GitHub publication

### Ownership

- Requested by: User
- Planned and executed by: Codex
- Repository: `https://github.com/mp-juns/universal-research-mcp`

### Publication

- Initialized this independent workspace as a new Git repository on `main`.
- Added a public-release `.gitignore` for workspace-managed directories,
  caches, Python bytecode, and derived index artifacts.
- Replaced local absolute paths in public documentation, profile, and example
  configuration with portable placeholders before committing.
- Created initial commit `9922b7b` (`Add universal research operations
  framework`) and pushed it to public `origin/main`.
- A draft PR was not created because this is the initial commit on the new
  repository's default branch.

### Verification

- Core, framework, and E2E unittest checks passed (9 tests total).
- Derived lexical/semantic builder regression checks passed (19 tests total).
- Research workflow Skill and Codex plugin manifest validation passed.
- Final branch state after initial push: `main...origin/main` with no pending
  changes at that point.

### Unexpected verification handling

- The direct `pytest` launcher did not include the workspace root in import
  resolution, so it could not find `scripts`. The test was rerun with
  `python3 -m pytest`, which passed. No source or data was changed as a result.
- The development environment lacks the optional MCP Python runtime. Pure core
  tests therefore do not import MCP transport; no dependency was installed.

## 2026-08-04 — review P0 plan: Core retrieval and approval hardening

### Ownership and scope

- Requested by: User
- Planned and executed by: Codex
- Decision: implement only the review's P0 integration and integrity findings;
  defer package/distribution, license, and full schema-validator parity work.

### Planned changes

- Add a derived-only Core-to-index projection that maps `record_id`,
  `record_kind`, `source_refs`, and `relations[].target_id` into the existing
  lexical/semantic document contract without altering canonical JSONL.
- Resolve Core amendments before indexing, alongside existing legacy correction
  handling.
- Require an existing `approval` record that is approved, human-issued, and
  explicitly scoped before the append helper accepts a record.
- Have evidence fetch report indexed and current SHA-256 values plus a bounded
  integrity status.

### Verification plan

- Run the core, framework, Core MCP E2E, lexical-builder, and semantic-builder
  fixture tests. The tests use temporary directories only and do not create or
  modify the project ledger or derived indexes.

### Results

- Added `core/indexing.py`; its projection is derived-only and keeps the Core
  record itself as the `raw_json` authority in SQLite.
- Both index builders now distinguish Core from legacy input, apply the
  appropriate append-only correction mechanism, and project Core fields before
  retrieval indexing.
- `append_approved_record` now rejects nonexistent, non-approval, non-approved,
  AI-issued, unscoped, and out-of-scope approval references.
- `memory_fetch_evidence` now reports `indexed_sha256`, `current_sha256`, and
  `integrity_status`; `sha256` remains a compatibility alias for the current
  file hash. Read-only SQLite connections now close explicitly.
- The E2E fixture uses a Core claim and protocol, verifies the relational
  projection and raw Core provenance, confirms a matched hash, then mutates its
  temporary source and confirms `mismatched` status.

### Verification outcomes

- `python3 -m unittest discover -s tests -p 'test_core_ledger.py'`: 4 passed.
- `python3 -m unittest discover -s tests -p 'test_framework_operations.py'`: 6 passed.
- `python3 -m unittest discover -s tests -p 'test_research_memory_e2e.py'`: 1 passed.
- `python3 -m pytest -q tests/test_build_research_ledger_index.py tests/test_build_research_semantic_index.py`: 20 passed.
- No warnings remained after explicitly closing MCP read-only SQLite
  connections. No reference-project, canonical-project-ledger, historical-log,
  network, or daemon operation occurred.
