from __future__ import annotations

from pathlib import Path

from universal_research_mcp.runtime.semantic_config import configure_local
from universal_research_mcp.semantic_runtime import _local_embedder, configured_backend


def test_configured_local_backend_reuses_same_process_embedder(tmp_path: Path) -> None:
    _local_embedder.cache_clear()
    try:
        configure_local(
            tmp_path,
            model_path=tmp_path / "offline-model",
            device="cpu",
            trust_local_model_code=False,
        )

        first = configured_backend(tmp_path)
        second = configured_backend(tmp_path)

        assert first is not None
        assert second is not None
        assert first.embedder is second.embedder
    finally:
        _local_embedder.cache_clear()


def test_configured_local_backend_separates_different_execution_policy(tmp_path: Path) -> None:
    _local_embedder.cache_clear()
    try:
        model_path = tmp_path / "offline-model"
        configure_local(
            tmp_path,
            model_path=model_path,
            device="cpu",
            trust_local_model_code=False,
        )
        untrusted = configured_backend(tmp_path)

        configure_local(
            tmp_path,
            model_path=model_path,
            device="cpu",
            trust_local_model_code=True,
        )
        trusted = configured_backend(tmp_path)

        assert untrusted is not None
        assert trusted is not None
        assert untrusted.embedder is not trusted.embedder
    finally:
        _local_embedder.cache_clear()
