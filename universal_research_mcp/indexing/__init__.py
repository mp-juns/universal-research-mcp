"""Safe construction of rebuildable research indexes."""

from .lexical import (
    canonical_fingerprint,
    ensure_lexical_index,
    index_status,
    initialize_project,
    validate_registered_sources,
    verify_lexical_index,
)
from .semantic import ensure_semantic_index, normalize_vector, semantic_status

__all__ = [
    "canonical_fingerprint",
    "ensure_lexical_index",
    "ensure_semantic_index",
    "index_status",
    "initialize_project",
    "normalize_vector",
    "semantic_status",
    "validate_registered_sources",
    "verify_lexical_index",
]
