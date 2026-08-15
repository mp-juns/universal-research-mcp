# ADR-0001: Canonical authority and derived views

Status: accepted

Canonical research history is append-only JSONL under `data/events`. SQLite,
FTS, and dense indexes are rebuildable projections and never outrank the source
ledger or registered original files. Retrieval returns candidates; important
claims require exact source-range fetch and current revision verification.

This separation permits index repair without rewriting research history and
makes stale or corrupted derived state fail closed.
