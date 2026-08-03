# MCP Layer

`project_search/` is the initial migrated compatibility implementation. It is
preserved for reference while `research_memory/` is the local, generic,
read-only MCP adapter used by the plugin.

The generic MCP exposes this read-only contract:

- `memory_search_candidates`: retrieve lexical candidate records
- `memory_latest`: retrieve chronologically ordered recent records
- `memory_fetch_evidence`: load source lines, compare the indexed and current
  SHA-256 values, and report `matched`, `mismatched`, `not_indexed`, or
  `ambiguous` integrity status
- `research_search`, `research_latest`, and `research_fetch`: compatibility aliases

Candidate results are never evidence. Material conclusions require an explicit
fetch of the returned source/artifact range. The MCP does not expose write,
approval, amendment, model-loading, daemon, or remote-network tools.
