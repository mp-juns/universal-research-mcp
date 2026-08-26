"""Supported offline semantic embedding backends.

These adapters are intentionally separate from the repository's experimental
generation-provider prototypes.  They never contact a network service and the
local SentenceTransformer path accepts only an already-present snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib.util
import math
from pathlib import Path
import re
from typing import Sequence

from universal_research_mcp.runtime.model_snapshot import SnapshotIdentity, verify_snapshot

DEFAULT_DIMENSIONS = 256
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str

    @classmethod
    def ready(cls) -> "Availability":
        return cls(True, "available")

    @classmethod
    def unavailable(cls, reason: str) -> "Availability":
        return cls(False, reason)


@dataclass(frozen=True)
class EmbeddingResult:
    request_id: str
    provider_id: str
    model: str
    vectors: tuple[tuple[float, ...], ...]


def _features(text: str):
    for match in _TOKEN.finditer(text.casefold()):
        token = match.group(0)
        yield f"token:{token}"
        padded = f"^{token}$"
        for offset in range(max(1, len(padded) - 2)):
            yield f"trigram:{padded[offset:offset + 3]}"


def encode_signed_hashing(
    text: str, dimensions: int = DEFAULT_DIMENSIONS,
) -> tuple[float, ...]:
    """Encode one non-empty text as a deterministic unit vector."""

    if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 8:
        raise ValueError("signed hashing dimensions must be at least 8")
    values = [0.0] * dimensions
    for feature in _features(text):
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dimensions
        values[bucket] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(math.fsum(value * value for value in values))
    if norm == 0:
        raise ValueError("semantic query must contain at least one word character")
    return tuple(value / norm for value in values)


class SignedHashingEmbedder:
    """No-download, no-network semantic adapter with an explicit demo label."""

    provider_id = "deterministic_demo"
    model = "signed_hashing_v1"

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def embed(
        self, texts: tuple[str, ...], *, model: str, dimensions: int | None,
    ) -> EmbeddingResult:
        if model != self.model:
            raise ValueError("signed hashing model identity is invalid")
        width = self.dimensions if dimensions is None else dimensions
        if width != self.dimensions:
            raise ValueError("signed hashing dimensions do not match its configuration")
        return EmbeddingResult(
            request_id="semantic-deterministic-demo",
            provider_id=self.provider_id,
            model=self.model,
            vectors=tuple(encode_signed_hashing(text, width) for text in texts),
        )


@dataclass
class LocalSentenceTransformerEmbedder:
    """Load only an explicit local snapshot and never download a model."""

    model_path: str | Path
    device: str = "auto"
    trust_local_model_code: bool = False
    provider_id: str = "local"
    snapshot: SnapshotIdentity | None = None
    _encoder: object | None = field(init=False, default=None, repr=False)

    @property
    def model_identity(self) -> str:
        path = str(Path(self.model_path).expanduser().resolve())
        return f"{path}@sha256:{self.snapshot.manifest_sha256}" if self.snapshot is not None else path

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
        self, texts: tuple[str, ...], *, model: str, dimensions: int | None,
    ) -> EmbeddingResult:
        readiness = self.preflight()
        if not readiness.available:
            raise RuntimeError(readiness.reason)
        snapshot = Path(self.model_path).expanduser().resolve()
        matches = model == self.model_identity if self.snapshot is not None else Path(model).expanduser().resolve() == snapshot
        if not matches:
            raise ValueError("semantic model does not match the approved local snapshot")
        encoder = self._encoder
        if encoder is None:
            encoder = self._load_encoder(snapshot)
            self._encoder = encoder
        encode = getattr(encoder, "encode")
        vectors = encode(
            list(texts), show_progress_bar=False, convert_to_numpy=False,
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
            request_id="semantic-local", provider_id=self.provider_id,
            model=self.model_identity, vectors=tuple(materialized),
        )

    def _load_encoder(self, snapshot: Path) -> object:
        if self.snapshot is not None:
            verify_snapshot(snapshot, self.snapshot)
        from sentence_transformers import SentenceTransformer

        selected_device = None if self.device == "auto" else self.device
        return SentenceTransformer(
            str(snapshot), device=selected_device, local_files_only=True,
            trust_remote_code=self.trust_local_model_code,
        )


__all__ = [
    "DEFAULT_DIMENSIONS", "Availability", "EmbeddingResult",
    "LocalSentenceTransformerEmbedder", "SignedHashingEmbedder",
    "encode_signed_hashing",
]
