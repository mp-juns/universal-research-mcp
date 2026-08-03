# MCP Layer

`project_search/` is the initial migrated compatibility implementation. It is
preserved for reference while `research_memory/` is the local, generic,
read-only MCP adapter used by the plugin.

The compatibility tree contains historical daemon/provider experiments,
including Ollama-oriented code. It is not packaged or registered as a supported
agent backend by the Codex-only preview.

The generic MCP exposes this canonical-read-only contract:

- `memory_search_candidates`: retrieve lexical candidate records
- `memory_latest`: retrieve chronologically ordered recent records
- `memory_fetch_evidence`: load source lines, compare the indexed and current
  SHA-256 values, and report `matched` or `mismatched`; unregistered or
  ambiguous revisions fail closed before a file is read
- `research_search`, `research_latest`, and `research_fetch`: compatibility
  aliases exposed only with `--legacy-tools` or the documented environment flag

Candidate results are never evidence. Material conclusions require an explicit
fetch of the returned source/artifact range. The MCP does not expose canonical
write, approval, amendment, model-loading, daemon, or remote-network execution
tools. The unified management CLI may create verified derived indexes outside
the MCP tool surface.

The 0.3.0 preview supports the Codex plugin only. Codex owns model selection,
native agent sessions, tool execution, and approvals. The plugin does not
register a provider-backed execution MCP and does not support Ollama, OpenAI
API, Anthropic API, Moonshot/Kimi, Claude Code, OpenCode, or OpenClaw.

## Launching

Install the repository package, then run the stable entry point from a research
project root:

```bash
universal-research serve --root /path/to/research-project
```

The entry point defaults to `UNIVERSAL_RESEARCH_ROOT`, then its current working
directory. Optional `--lexical-db` and `--events-root` arguments override only
the derived index and canonical event locations. The Codex plugin invokes the
`universal-research` entry point directly, so it does not depend on a repository-relative
`../../mcp/...` launcher path.

No provider-backed execution server is started by this command or registered
by the Codex plugin.
