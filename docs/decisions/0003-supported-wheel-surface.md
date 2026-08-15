# ADR-0003: Supported wheel versus experimental source

Status: accepted

The public `universal-research-mcp` wheel contains research memory, governance,
offline semantic retrieval, secure harness, and Codex integration. Repository
prototypes for OpenAI/Anthropic generation providers, plugin-owned agent runtime,
and provider execution harness are excluded from the wheel and are not public
Python APIs.

Keeping prototype source in the repository supports continued design research
without implying credentials, provider compatibility, model entitlement, or a
stable runtime contract. A future provider release requires a separate package,
threat model, API review, and release lifecycle.
