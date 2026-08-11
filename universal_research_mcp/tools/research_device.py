"""Portable PyTorch accelerator selection for research tools.

This module deliberately has no top-level PyTorch import.  Lexical-only
commands and explicit CPU usage therefore stay usable in minimal Linux
environments, while CUDA/MPS requests fail with a clear diagnostic.
"""

from __future__ import annotations

from typing import Any


DEVICE_CHOICES = ("auto", "cpu", "cuda", "mps")


def _load_torch(torch_module: Any | None) -> Any:
    if torch_module is not None:
        return torch_module
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - CLI environment behavior
        raise RuntimeError(
            "PyTorch is required for CUDA/MPS selection. Install it with "
            "a build matching the host, or request --device cpu."
        ) from exc
    return torch


def accelerator_availability(torch_module: Any | None = None) -> dict[str, bool]:
    """Return backend availability without assuming an Apple-only torch build."""
    torch = _load_torch(torch_module)
    cuda = getattr(torch, "cuda", None)
    backends = getattr(torch, "backends", None)
    mps = getattr(backends, "mps", None)
    return {
        "cuda": bool(cuda and cuda.is_available()),
        "mps": bool(mps and mps.is_available()),
    }


def resolve_torch_device(requested: str, torch_module: Any | None = None) -> str:
    """Resolve ``auto|cuda|mps|cpu`` with strict explicit-device behavior.

    CUDA is preferred for portable Linux/WSL use.  MPS remains available for
    the existing Mac workflow.  Explicit unavailable accelerators are errors,
    because silently selecting CPU would invalidate a user's intended run.
    """
    normalized = requested.casefold().strip()
    base_device = "cuda" if normalized.startswith("cuda:") else normalized
    if base_device not in DEVICE_CHOICES:
        raise ValueError(f"Unsupported device {requested!r}; choose one of {', '.join(DEVICE_CHOICES)}")
    if base_device == "cpu":
        return "cpu"
    available = accelerator_availability(torch_module)
    if base_device == "auto":
        if available["cuda"]:
            return "cuda"
        if available["mps"]:
            return "mps"
        return "cpu"
    if not available[base_device]:
        raise RuntimeError(
            f"Requested --device {normalized}, but this PyTorch runtime has no available {base_device} backend. "
            "Use --device auto/cpu, or install a matching accelerator-enabled PyTorch build."
        )
    return normalized
