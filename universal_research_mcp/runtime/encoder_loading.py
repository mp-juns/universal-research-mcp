"""Shared loading rules for local SentenceTransformer encoders.

The supported runtime backend and the standalone builder both load pinned
local snapshots and both have to survive the same Transformers behaviour.
Keeping one implementation here prevents the two from drifting apart, which
already happened once: the builder carried a checkpoint-reload workaround that
the runtime backend never received, so the supported path embedded with
randomly initialized weights while reporting success.

Nothing here contacts a network service or selects a model.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# A loaded encoder is compared against its own checkpoint before first use.
# Sampling a few contiguous rows per tensor keeps the check cheap while still
# separating checkpoint weights from any freshly initialized distribution.
_CHECKPOINT_SAMPLE_ROWS = 4
_CHECKPOINT_WHOLE_TENSOR_ELEMENTS = 1 << 16
_CHECKPOINT_ABS_TOLERANCE = 1e-4
_CHECKPOINT_REL_TOLERANCE = 1e-3

# The RoPE constructor always yields the frequencies; whether it also caches
# cosines and sines depends on the pinned revision, so those are validated when
# the model builds them rather than demanded.
_GTE_REQUIRED_BUFFERS = ("embeddings.position_ids", "embeddings.rotary_emb.inv_freq")
_GTE_CACHED_BUFFERS = ("embeddings.rotary_emb.cos_cached", "embeddings.rotary_emb.sin_cached")


@contextmanager
def checkpoint_weights_guarded() -> Iterator[None]:
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

    def guarded(self: Any, module: Any) -> Any:
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


def encoder_base_model(encoder: Any) -> Any:
    """Return the Transformers backbone held by a SentenceTransformer."""

    try:
        transformer = encoder[0]
    except (AttributeError, IndexError, KeyError, TypeError):
        transformer = None
    base_model = getattr(transformer, "auto_model", None)
    if base_model is not None:
        return base_model
    modules = getattr(encoder, "modules", None)
    if callable(modules):
        for module in modules():
            candidate = getattr(module, "auto_model", None)
            if candidate is not None and hasattr(candidate, "state_dict"):
                return candidate
    raise ValueError("local encoder does not expose a Transformers backbone (module 0 auto_model)")


def checkpoint_files(snapshot: Path) -> list[Path]:
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


def _tolerances(dtype: Any) -> tuple[float, float]:
    """Widen the comparison to the resident dtype's own resolution.

    The builder may load directly in bfloat16, whose ~3 significant decimal
    digits cannot represent a float16 checkpoint exactly.  Reinitialized
    weights differ from trained ones by orders of magnitude, so the widened
    bound still separates them decisively.
    """

    import torch

    try:
        epsilon = float(torch.finfo(dtype).eps)
    except (TypeError, ValueError):
        return _CHECKPOINT_REL_TOLERANCE, _CHECKPOINT_ABS_TOLERANCE
    return max(_CHECKPOINT_REL_TOLERANCE, 2 * epsilon), max(_CHECKPOINT_ABS_TOLERANCE, epsilon)


def _comparable_parts(stored: Any, actual: Any) -> list[tuple[Any, Any]]:
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
        rows: Any = range(count)
    else:
        step = count // _CHECKPOINT_SAMPLE_ROWS
        rows = (min(index * step, count - 1) for index in range(_CHECKPOINT_SAMPLE_ROWS))
    return [(stored[row:row + 1], actual[row:row + 1]) for row in rows]


def verify_checkpoint_weights(encoder: Any, snapshot: Path) -> dict[str, int]:
    """Fail closed unless the loaded backbone matches its own checkpoint.

    A silently reinitialized encoder stays deterministic inside one process, so
    neither a fingerprint nor a repeated call within a session can detect it.
    Comparing the resident parameters against the snapshot's own tensors is the
    only check that separates trained weights from a fresh distribution.
    """

    from safetensors import safe_open
    import torch

    base_model = encoder_base_model(encoder)
    checkpoints = checkpoint_files(snapshot)
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
                relative, absolute = _tolerances(actual.dtype)
                for expected_part, actual_part in _comparable_parts(stored, actual):
                    if not torch.allclose(
                        expected_part.to(torch.float32),
                        actual_part.detach().to(device="cpu", dtype=torch.float32),
                        rtol=relative,
                        atol=absolute,
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


def rebuild_gte_runtime_buffers(base_model: Any, *, match_parameter_dtype: bool) -> int:
    """Recreate GTE new-impl's nonpersistent position and RoPE buffers.

    Transformers 5 loads parameters on meta, but this older custom model does
    not initialize its non-checkpoint buffers afterwards. Reuse the model's own
    RoPE constructor and verify the result; never change checkpoint parameters.

    ``match_parameter_dtype`` casts the rebuilt RoPE cache to the parameter
    dtype, which the runtime backend needs because it may cast the encoder
    after loading. The builder loads directly in its target dtype and keeps the
    cache at the constructor's own precision.
    """

    import torch

    embeddings = getattr(base_model, "embeddings", None)
    config = getattr(base_model, "config", None)
    if embeddings is None or config is None:
        raise ValueError("local encoder base model lacks embeddings/config")
    if not hasattr(embeddings, "_init_rope") or not hasattr(embeddings, "word_embeddings"):
        raise ValueError("local encoder embeddings lack deterministic RoPE initialization")

    weight = embeddings.word_embeddings.weight
    position_ids = torch.arange(int(config.max_position_embeddings), device=weight.device)
    embeddings.register_buffer("position_ids", position_ids, persistent=False)
    if getattr(embeddings, "position_embedding_type", "rope") != "rope":
        return 1
    embeddings._init_rope(config)
    embeddings.rotary_emb.to(
        device=weight.device, **({"dtype": weight.dtype} if match_parameter_dtype else {})
    )

    buffers = dict(base_model.named_buffers())
    missing = sorted(set(_GTE_REQUIRED_BUFFERS) - buffers.keys())
    if missing:
        raise ValueError(f"local encoder buffer rebuild did not produce: {missing}")
    rebuilt = [name for name in (*_GTE_REQUIRED_BUFFERS, *_GTE_CACHED_BUFFERS) if name in buffers]
    for name in rebuilt:
        value = buffers[name]
        if value.is_floating_point() and not torch.isfinite(value).all():
            raise ValueError(f"local encoder buffer rebuild produced non-finite {name}")
    if not torch.equal(buffers["embeddings.position_ids"], position_ids):
        raise ValueError("local encoder position_ids reconstruction mismatch")
    # At position zero the rotation is the identity, which is the cheapest
    # end-to-end check that the cache was rebuilt rather than left stale.
    for name, expected in (
        ("embeddings.rotary_emb.cos_cached", torch.ones_like),
        ("embeddings.rotary_emb.sin_cached", torch.zeros_like),
    ):
        if name not in buffers:
            continue
        zero_position = buffers[name][0]
        if not torch.equal(zero_position, expected(zero_position)):
            raise ValueError(f"local encoder RoPE zero-position invariant failed for {name}")
    return len(rebuilt)


def restore_gte_runtime_buffers(encoder: Any) -> int:
    """Repair GTE backbones inside an encoder, leaving other models untouched.

    The runtime backend accepts any reviewed local snapshot, so detection is by
    architecture rather than by assumption; a non-GTE encoder needs no repair.
    """

    modules = getattr(encoder, "modules", None)
    if not callable(modules):
        return 0
    repaired = 0
    for model in tuple(modules()):
        config = getattr(model, "config", None)
        embeddings = getattr(model, "embeddings", None)
        if (type(model).__name__ != "NewModel"
                or getattr(config, "model_type", None) != "new"
                or type(embeddings).__name__ != "NewEmbeddings"):
            continue
        rebuild_gte_runtime_buffers(model, match_parameter_dtype=True)
        repaired += 1
    return repaired


__all__ = [
    "checkpoint_files", "checkpoint_weights_guarded", "encoder_base_model",
    "rebuild_gte_runtime_buffers", "restore_gte_runtime_buffers",
    "verify_checkpoint_weights",
]
