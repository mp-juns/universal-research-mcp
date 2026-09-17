"""Supported offline semantic embedding backends.

These adapters are intentionally separate from the repository's experimental
generation-provider prototypes.  They never contact a network service and the
local SentenceTransformer path accepts only an already-present snapshot.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import importlib.util
import math
from pathlib import Path
import re
import threading
from typing import Iterator, Sequence

from universal_research_mcp.runtime.model_snapshot import SnapshotIdentity, verify_snapshot

DEFAULT_DIMENSIONS = 256
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

# Bumped whenever a loader defect changed the numeric meaning of local
# embeddings.  It participates in the embedding identity so that indexes built
# by an affected release are reported stale instead of silently reused.
LOADER_GENERATION = "checkpoint-verified-v1"

# A loaded encoder is compared against its own checkpoint before first use.
# Sampling a few contiguous rows per tensor keeps the check cheap while still
# separating checkpoint weights from any freshly initialized distribution.
_CHECKPOINT_SAMPLE_ROWS = 4
_CHECKPOINT_WHOLE_TENSOR_ELEMENTS = 1 << 16
_CHECKPOINT_ABS_TOLERANCE = 1e-4
_CHECKPOINT_REL_TOLERANCE = 1e-3

# The reinitialization guard swaps a Transformers class attribute, so concurrent
# loads in one process are serialized.
_LOAD_LOCK = threading.Lock()


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
    encoder_dtype: str | None = None
    max_length: int | None = None
    _encoder: object | None = field(init=False, default=None, repr=False)

    @property
    def model_identity(self) -> str:
        path = str(Path(self.model_path).expanduser().resolve())
        identity = f"{path}@sha256:{self.snapshot.manifest_sha256}" if self.snapshot is not None else path
        if self.encoder_dtype is not None or self.max_length is not None:
            identity += f"#dtype={self.encoder_dtype};max_length={self.max_length}"
        return f"{identity}!loader={LOADER_GENERATION}"

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
        matches = model == self.model_identity
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
        with _LOAD_LOCK:
            with _checkpoint_weights_guarded():
                encoder = SentenceTransformer(
                    str(snapshot), device=selected_device, local_files_only=True,
                    trust_remote_code=self.trust_local_model_code,
                )
        # Verify before any dtype cast so the comparison stays in the
        # checkpoint's own precision.
        verify_encoder_checkpoint_weights(encoder, snapshot)
        if self.max_length is not None:
            encoder.max_seq_length = self.max_length
        if self.encoder_dtype is not None:
            import torch
            encoder.to(dtype=getattr(torch, self.encoder_dtype))
        _restore_gte_runtime_buffers(encoder)
        return encoder


@contextmanager
def _checkpoint_weights_guarded() -> Iterator[None]:
    """Stop Transformers 5 from reinitializing weights it already loaded.

    Transformers 5 builds the model on meta, installs the checkpoint tensors,
    and only then calls ``initialize_weights`` to fill whatever is still
    missing.  Parameters that came from the checkpoint are marked
    ``_is_hf_initialized`` and are expected to be skipped, but that flag only
    protects modules whose ``_init_weights`` either checks it or goes through
    the patched ``torch.nn.init`` helpers.  Older remote-code architectures
    instead mutate tensors directly (``module.weight.data.normal_()``,
    ``.zero_()``, ``.fill_()``), which no guard can intercept, so every
    parameter is overwritten with a fresh random distribution after a load that
    reported no missing keys.

    Skip modules whose tensors were all loaded, and restore the loaded ones for
    partially populated modules; anything genuinely missing is still
    initialized by the original implementation.
    """

    try:
        from transformers.modeling_utils import PreTrainedModel
    except ImportError:
        yield
        return
    original = getattr(PreTrainedModel, "_initialize_weights", None)
    if original is None:
        # A Transformers release without this hook does not run the offending
        # finalization step; verification still covers the result.
        yield
        return
    import torch

    def guarded(self: object, module: object) -> object:
        present = [
            tensor
            for tensor in (*module._parameters.values(), *module._buffers.values())
            if tensor is not None
        ]
        loaded = [tensor for tensor in present if getattr(tensor, "_is_hf_initialized", False)]
        if not loaded:
            return original(self, module)
        if len(loaded) == len(present):
            module._is_hf_initialized = True
            return None
        retained = [
            (tensor, tensor.detach().clone())
            for tensor in loaded
            if tensor.device.type != "meta"
        ]
        try:
            return original(self, module)
        finally:
            with torch.no_grad():
                for tensor, value in retained:
                    tensor.copy_(value)

    PreTrainedModel._initialize_weights = guarded
    try:
        yield
    finally:
        PreTrainedModel._initialize_weights = original


def _encoder_base_model(encoder: object) -> object:
    """Return the Transformers backbone held by a SentenceTransformer."""

    modules = getattr(encoder, "modules", None)
    if callable(modules):
        for module in modules():
            base_model = getattr(module, "auto_model", None)
            if base_model is not None and hasattr(base_model, "state_dict"):
                return base_model
    raise RuntimeError("local embedding encoder does not expose a Transformers backbone")


def _checkpoint_files(snapshot: Path) -> list[Path]:
    """Locate the backbone's safetensors shards inside a snapshot."""

    files = sorted(snapshot.glob("*.safetensors"))
    if not files:
        # SentenceTransformer layouts may keep the backbone in a numbered
        # module directory instead of the snapshot root.
        files = sorted(
            path
            for directory in sorted(snapshot.iterdir())
            if directory.is_dir()
            for path in directory.glob("*.safetensors")
        )
    return files


def _comparable_parts(stored: object, actual: object) -> list[tuple[object, object]]:
    """Pair checkpoint and resident slices that are cheap to read and compare.

    Small tensors are compared whole. Large ones would mean faulting in the
    entire checkpoint, so a few rows spread across the first dimension stand in;
    a reinitialized tensor differs from trained weights in essentially every
    element, so a spread sample separates the two just as reliably.
    """

    if actual.ndim == 0 or actual.numel() <= _CHECKPOINT_WHOLE_TENSOR_ELEMENTS:
        return [(stored[:], actual)]
    count = int(actual.shape[0])
    if count <= _CHECKPOINT_SAMPLE_ROWS:
        rows = range(count)
    else:
        step = count // _CHECKPOINT_SAMPLE_ROWS
        rows = (min(index * step, count - 1) for index in range(_CHECKPOINT_SAMPLE_ROWS))
    return [(stored[row:row + 1], actual[row:row + 1]) for row in rows]


def verify_encoder_checkpoint_weights(encoder: object, snapshot: Path) -> dict[str, int]:
    """Fail closed unless the loaded backbone matches its own checkpoint.

    A silently reinitialized encoder stays deterministic inside one process, so
    neither a fingerprint nor a repeated call within a session can detect it.
    Comparing the resident parameters against the snapshot's own tensors is the
    only check that separates trained weights from a fresh distribution.
    """

    from safetensors import safe_open
    import torch

    base_model = _encoder_base_model(encoder)
    checkpoints = _checkpoint_files(snapshot)
    if not checkpoints:
        raise RuntimeError(
            "local embedding snapshot has no .safetensors checkpoint, so its loaded "
            f"weights cannot be verified: {snapshot}"
        )
    resident = base_model.state_dict()
    prefix = getattr(type(base_model), "base_model_prefix", "") or ""
    # Every trained parameter must be accounted for. Buffers are derived rather
    # than trained, and which of them a checkpoint carries varies by Transformers
    # version, so they are compared when present but are not required.
    unverified = {name for name, _ in base_model.named_parameters()}
    compared = 0
    mismatched: list[str] = []
    for checkpoint in checkpoints:
        with safe_open(checkpoint, framework="pt", device="cpu") as handle:
            for key in handle.keys():
                name = key
                if name not in resident and prefix and name.startswith(f"{prefix}."):
                    name = name[len(prefix) + 1:]
                if name not in resident:
                    # A head the backbone does not carry, e.g. a classifier.
                    continue
                actual = resident[name]
                stored = handle.get_slice(key)
                if tuple(stored.get_shape()) != tuple(actual.shape):
                    mismatched.append(name)
                    unverified.discard(name)
                    continue
                for expected_part, actual_part in _comparable_parts(stored, actual):
                    if not torch.allclose(
                        expected_part.to(torch.float32),
                        actual_part.detach().to(device="cpu", dtype=torch.float32),
                        rtol=_CHECKPOINT_REL_TOLERANCE,
                        atol=_CHECKPOINT_ABS_TOLERANCE,
                    ):
                        mismatched.append(name)
                        break
                compared += 1
                unverified.discard(name)
    if mismatched or unverified or not compared:
        raise RuntimeError(
            "local embedding encoder does not match its checkpoint, so its vectors "
            "would be meaningless; refusing to embed. "
            f"snapshot={snapshot} compared={compared} "
            f"mismatched={sorted(set(mismatched))[:8]} "
            f"unverified={sorted(unverified)[:8]}"
        )
    return {"compared": compared, "checkpoints": len(checkpoints)}


def _restore_gte_runtime_buffers(encoder: object) -> None:
    """Recreate GTE new-impl's nonpersistent buffers after meta loading.

    Transformers 5 loads parameters on meta, but this older custom model does
    not initialize its non-checkpoint position/RoPE buffers afterwards. Reuse
    the model's own RoPE constructor; never change checkpoint parameters.
    """
    modules = getattr(encoder, "modules", None)
    if not callable(modules):
        return
    for model in tuple(modules()):
        config = getattr(model, "config", None)
        embeddings = getattr(model, "embeddings", None)
        if (type(model).__name__ != "NewModel"
                or getattr(config, "model_type", None) != "new"
                or type(embeddings).__name__ != "NewEmbeddings"):
            continue
        import torch
        weight = embeddings.word_embeddings.weight
        embeddings.register_buffer(
            "position_ids", torch.arange(config.max_position_embeddings, device=weight.device),
            persistent=False,
        )
        if embeddings.position_embedding_type == "rope":
            embeddings._init_rope(config)
            embeddings.rotary_emb.to(device=weight.device, dtype=weight.dtype)


__all__ = [
    "DEFAULT_DIMENSIONS", "LOADER_GENERATION", "Availability", "EmbeddingResult",
    "LocalSentenceTransformerEmbedder", "SignedHashingEmbedder",
    "encode_signed_hashing", "verify_encoder_checkpoint_weights",
]
