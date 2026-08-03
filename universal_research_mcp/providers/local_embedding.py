"""Optional local-only sentence-transformer embedding boundary."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
from typing import Sequence

from .contracts import Availability, EmbeddingResult


@dataclass
class LocalSentenceTransformerEmbedder:
    """Load only an explicit local snapshot and never download a model."""

    model_path: str | Path
    device: str = "auto"
    trust_local_model_code: bool = False
    provider_id: str = "local"

    def preflight(self) -> Availability:
        snapshot = Path(self.model_path).expanduser().resolve()
        if not snapshot.exists():
            return Availability.unavailable("configured local embedding snapshot does not exist")
        if importlib.util.find_spec("sentence_transformers") is None:
            return Availability.unavailable("sentence-transformers is not installed")
        if importlib.util.find_spec("torch") is None:
            return Availability.unavailable("torch is not installed")
        if self.device not in {"auto", "cpu", "cuda", "mps"}:
            return Availability.unavailable("configured local embedding device is invalid")
        if self.device == "cuda":
            try:
                import torch
            except ImportError:
                return Availability.unavailable("torch is not installed")
            if not torch.cuda.is_available():
                return Availability.unavailable("CUDA was requested but is unavailable")
        return Availability.ready()

    def embed(
        self,
        texts: tuple[str, ...],
        *,
        model: str,
        dimensions: int | None,
    ) -> EmbeddingResult:
        readiness = self.preflight()
        if not readiness.available:
            raise RuntimeError(readiness.reason)
        snapshot = Path(self.model_path).expanduser().resolve()
        if Path(model).expanduser().resolve() != snapshot:
            raise ValueError("semantic model does not match the approved local snapshot")
        from sentence_transformers import SentenceTransformer

        selected_device = None if self.device == "auto" else self.device
        encoder = SentenceTransformer(
            str(snapshot),
            device=selected_device,
            local_files_only=True,
            trust_remote_code=self.trust_local_model_code,
        )
        vectors = encoder.encode(
            list(texts),
            show_progress_bar=False,
            convert_to_numpy=False,
            normalize_embeddings=False,
        )
        materialized: list[tuple[float, ...]] = []
        for vector in vectors:
            tolist = getattr(vector, "tolist", None)
            values: Sequence[float] = tolist() if callable(tolist) else vector
            materialized.append(tuple(float(value) for value in values))
        if dimensions is not None:
            materialized = [vector[:dimensions] for vector in materialized]
        return EmbeddingResult(
            request_id="semantic-local",
            provider_id=self.provider_id,
            model=str(snapshot),
            vectors=tuple(materialized),
        )
