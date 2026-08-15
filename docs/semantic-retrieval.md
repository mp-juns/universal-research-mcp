# Local semantic retrieval

Universal Research supports lexical retrieval by default and optional offline
semantic/hybrid retrieval. The supported PyPI surface includes only:

- deterministic signed hashing for demos and lifecycle checks;
- an explicitly configured, already-present local SentenceTransformer snapshot.

Use `universal-research semantic models` to inspect the reviewed catalogue and
`semantic setup` to prepare a hash-bound environment plan. Package installation,
model download, GPU use, and execution require separate host approval. The
runtime never silently falls back to a remote embedding API.

Semantic results remain candidates. A current semantic index does not replace
exact source fetch, SHA-256 verification, evidence eligibility, or semantic
relevance/conflict review.
