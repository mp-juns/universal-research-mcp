"""Offline signed-hashing vectors for demos, tests, and semantic UX checks.

This is deliberately *not* a trained embedding model. It makes the complete
semantic/hybrid lifecycle testable without a download, GPU, credential, or
network request.
"""

from __future__ import annotations

import hashlib
import math
import re

from .contracts import EmbeddingResult


DEFAULT_DIMENSIONS = 256
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _features(text: str):
    for match in _TOKEN.finditer(text.casefold()):
        token = match.group(0)
        yield f"token:{token}"
        padded = f"^{token}$"
        for offset in range(max(1, len(padded) - 2)):
            yield f"trigram:{padded[offset:offset + 3]}"


def encode_signed_hashing(text: str, dimensions: int = DEFAULT_DIMENSIONS) -> tuple[float, ...]:
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

    def embed(self, texts: tuple[str, ...], *, model: str, dimensions: int | None) -> EmbeddingResult:
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


__all__ = ["DEFAULT_DIMENSIONS", "SignedHashingEmbedder", "encode_signed_hashing"]
