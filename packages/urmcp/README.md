# urmcp

`urmcp` is the short PyPI install name for
[Universal Research MCP](https://github.com/mp-juns/universal-research-mcp).

```bash
python -m pip install --upgrade urmcp
urmcp --help
```

It intentionally contains no second MCP implementation. Installing it resolves
the exactly matching `universal-research-mcp` release, then exposes the same
`urmcp` command. The full distribution name remains available for users who
prefer an explicit package name:

```bash
python -m pip install --upgrade universal-research-mcp
```

See the [Korean user guide](https://github.com/mp-juns/universal-research-mcp/blob/main/docs/user-guide.md)
for installation, Codex integration, source registration, guarded ingestion,
semantic retrieval, troubleshooting, and operational boundaries.
