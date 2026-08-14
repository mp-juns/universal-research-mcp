"""Resolve explicitly configured offline semantic backends.

This is shared by the management CLI and the read-only MCP query path. It
never selects a network provider, downloads a model, or falls back between
backends.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_research_mcp.providers.deterministic_embedding import SignedHashingEmbedder
from universal_research_mcp.providers.local_embedding import LocalSentenceTransformerEmbedder
from universal_research_mcp.runtime.semantic_config import load_semantic_config
from universal_research_mcp.runtime.research_profile import semantic_config_from_profile


@dataclass(frozen=True)
class SemanticBackend:
    embedder: Any
    provider_id: str
    model: str
    dimensions: int | None
    backend_class: str
    trained_embedding_model: bool
    auto_refresh: bool


def configured_backend(root: str | Path) -> SemanticBackend | None:
    # An explicit semantic.json remains the narrower, higher-priority setting.
    # A validated profile is only a declarative fallback; neither path performs
    # downloads or network requests.
    config = load_semantic_config(root) or semantic_config_from_profile(root)
    if config is None:
        return None
    backend = config["backend"]
    if backend["kind"] == "signed_hashing_v1":
        dimensions = int(backend["dimensions"])
        return SemanticBackend(
            embedder=SignedHashingEmbedder(dimensions),
            provider_id="deterministic_demo",
            model="signed_hashing_v1",
            dimensions=dimensions,
            backend_class="deterministic_demo",
            trained_embedding_model=False,
            auto_refresh=bool(config["auto_refresh"]),
        )
    if backend["kind"] == "local_sentence_transformer":
        model_path = str(Path(backend["model_path"]).expanduser().resolve())
        return SemanticBackend(
            embedder=LocalSentenceTransformerEmbedder(
                model_path,
                device=backend["device"],
                trust_local_model_code=backend["trust_local_model_code"],
            ),
            provider_id="local",
            model=model_path,
            dimensions=backend["dimensions"],
            backend_class="local_trained_model",
            trained_embedding_model=True,
            auto_refresh=bool(config["auto_refresh"]),
        )
    raise ValueError("semantic backend is not supported")


def build_configured_semantic_index(root: str | Path, *, batch_size: int = 32) -> dict[str, Any]:
    """Build the configured derived semantic view without fallback or network."""

    from universal_research_mcp.indexing import ensure_lexical_index, ensure_semantic_index

    backend = configured_backend(root)
    if backend is None:
        return {
            "status": "setup_required",
            "reason": "configure an offline semantic backend before building the semantic index",
            "remote_used": False,
        }
    if backend.provider_id == "local":
        readiness = backend.embedder.preflight()
        if not readiness.available:
            return {
                "status": "setup_required",
                "reason": readiness.reason,
                "remote_used": False,
                "backend_class": backend.backend_class,
                "trained_embedding_model": backend.trained_embedding_model,
            }
    lexical = ensure_lexical_index(root)
    report = ensure_semantic_index(
        root,
        backend.embedder,
        provider_id=backend.provider_id,
        model=backend.model,
        dimensions=backend.dimensions,
        batch_size=batch_size,
    )
    return {
        "status": report["status"],
        "lexical": lexical,
        "semantic": report,
        "backend_class": backend.backend_class,
        "trained_embedding_model": backend.trained_embedding_model,
        "remote_used": False,
    }


__all__ = ["SemanticBackend", "build_configured_semantic_index", "configured_backend"]
