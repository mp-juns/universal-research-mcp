"""Regression tests for local encoder checkpoint loading.

A silently reinitialized encoder stays deterministic inside a single process,
so these tests compare against the checkpoint itself and across process
boundaries rather than against a repeated call in one session.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from universal_research_mcp.runtime.encoder_loading import (
    checkpoint_weights_guarded,
    verify_checkpoint_weights,
)

torch = pytest.importorskip(
    "torch", reason="local encoder loading requires the semantic extras"
)
pytest.importorskip("transformers", reason="local encoder loading requires the semantic extras")
pytest.importorskip("safetensors", reason="local encoder loading requires the semantic extras")

REPO_ROOT = Path(__file__).resolve().parents[1]

# A minimal stand-in for the older remote-code architectures the supported
# catalogue pins. Its `_init_weights` mutates tensors in place, which is exactly
# what the Transformers init guard cannot intercept.
TINY_ENCODER_SOURCE = '''
import torch
from torch import nn
from transformers import PretrainedConfig, PreTrainedModel

FILL = {"embeddings.weight": 0.25, "dense.weight": 0.5, "dense.bias": 0.125}


class TinyConfig(PretrainedConfig):
    model_type = "urmcp_tiny"

    def __init__(self, hidden_size=8, vocab_size=16, **kwargs):
        self.hidden_size = hidden_size
        self.vocab_size = vocab_size
        super().__init__(**kwargs)


class TinyModel(PreTrainedModel):
    config_class = TinyConfig
    base_model_prefix = "tiny"

    def __init__(self, config):
        super().__init__(config)
        self.embeddings = nn.Embedding(config.vocab_size, config.hidden_size)
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.post_init()

    def _init_weights(self, module):
        if isinstance(module, (nn.Linear, nn.Embedding)):
            module.weight.data.normal_(mean=0.0, std=0.5)
        if isinstance(module, nn.Linear) and module.bias is not None:
            module.bias.data.zero_()

    def forward(self, input_ids):
        return self.dense(self.embeddings(input_ids))


class TinyEncoder(nn.Module):
    """Stands in for SentenceTransformer's `auto_model`-bearing module."""

    def __init__(self, auto_model):
        super().__init__()
        self.auto_model = auto_model


def build(path):
    model = TinyModel(TinyConfig())
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            parameter.fill_(FILL[name])
    model.save_pretrained(path)
    return model


def load(path):
    return TinyModel.from_pretrained(path)


def embed(model):
    with torch.no_grad():
        output = model(torch.arange(4).unsqueeze(0))
    return [round(float(value), 6) for value in output.flatten().tolist()]
'''


@pytest.fixture
def tiny_snapshot(tmp_path: Path):
    """Write the stand-in architecture and a saved checkpoint for it."""

    module = tmp_path / "tiny_encoder.py"
    module.write_text(TINY_ENCODER_SOURCE, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        import tiny_encoder

        snapshot = tmp_path / "snapshot"
        reference = tiny_encoder.build(snapshot)
        yield tiny_encoder, snapshot, reference
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("tiny_encoder", None)


def test_guarded_load_keeps_checkpoint_weights(tiny_snapshot) -> None:
    tiny_encoder, snapshot, reference = tiny_snapshot

    with checkpoint_weights_guarded():
        loaded = tiny_encoder.load(snapshot)

    expected = reference.state_dict()
    for name, parameter in loaded.state_dict().items():
        assert torch.equal(parameter, expected[name]), name


def test_both_encoder_entry_points_share_one_implementation() -> None:
    """The builder's bridge and the runtime backend must not drift apart again.

    The defect this module guards against existed because the standalone
    builder carried a fix the supported runtime backend never received.
    """

    from universal_research_mcp import semantic_backends
    from universal_research_mcp.runtime import encoder_loading
    from universal_research_mcp.tools import build_research_semantic_index as builder

    assert semantic_backends._checkpoint_weights_guarded is encoder_loading.checkpoint_weights_guarded
    assert builder.checkpoint_weights_guarded is encoder_loading.checkpoint_weights_guarded
    assert semantic_backends.verify_encoder_checkpoint_weights is encoder_loading.verify_checkpoint_weights
    assert builder.verify_checkpoint_weights is encoder_loading.verify_checkpoint_weights
    assert semantic_backends._restore_gte_runtime_buffers is encoder_loading.restore_gte_runtime_buffers
    assert builder.rebuild_gte_runtime_buffers is encoder_loading.rebuild_gte_runtime_buffers


def test_guard_restores_the_transformers_hook() -> None:
    from transformers.modeling_utils import PreTrainedModel

    original = PreTrainedModel._initialize_weights
    with checkpoint_weights_guarded():
        assert PreTrainedModel._initialize_weights is not original
    assert PreTrainedModel._initialize_weights is original


def test_guard_forwards_whatever_signature_transformers_uses() -> None:
    """Transformers changes this hook's arguments between releases.

    5.17 added `is_custom_code`, which raised TypeError against a guard that
    pinned the older two-argument form and took the local backend down
    entirely on an upgraded runtime.
    """

    from transformers.modeling_utils import PreTrainedModel

    seen: list[tuple] = []

    def original(self, module, *arguments, **keywords):
        seen.append((arguments, keywords))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(PreTrainedModel, "_initialize_weights", original, raising=False)
        with checkpoint_weights_guarded():
            guarded = PreTrainedModel._initialize_weights
            bare = torch.nn.Linear(2, 2)
            guarded(None, bare)
            guarded(None, bare, True)
            guarded(None, bare, is_custom_code=True)

    assert seen == [((), {}), ((True,), {}), ((), {"is_custom_code": True})]


def test_verification_accepts_a_faithfully_loaded_encoder(tiny_snapshot) -> None:
    tiny_encoder, snapshot, _ = tiny_snapshot

    with checkpoint_weights_guarded():
        loaded = tiny_encoder.load(snapshot)
    report = verify_checkpoint_weights(tiny_encoder.TinyEncoder(loaded), snapshot)

    assert report["compared"] == 3
    assert report["checkpoints"] == 1


def test_verification_refuses_reinitialized_weights(tiny_snapshot) -> None:
    tiny_encoder, snapshot, _ = tiny_snapshot

    with checkpoint_weights_guarded():
        loaded = tiny_encoder.load(snapshot)
    with torch.no_grad():
        loaded.embeddings.weight.normal_(mean=0.0, std=0.5)

    with pytest.raises(RuntimeError, match="does not match its checkpoint"):
        verify_checkpoint_weights(tiny_encoder.TinyEncoder(loaded), snapshot)


def test_verification_refuses_a_parameter_absent_from_the_checkpoint(tiny_snapshot) -> None:
    tiny_encoder, snapshot, _ = tiny_snapshot

    with checkpoint_weights_guarded():
        loaded = tiny_encoder.load(snapshot)
    loaded.register_parameter("unbacked", torch.nn.Parameter(torch.zeros(2)))

    with pytest.raises(RuntimeError, match="unverified="):
        verify_checkpoint_weights(tiny_encoder.TinyEncoder(loaded), snapshot)


def test_verification_refuses_a_snapshot_without_a_checkpoint(tiny_snapshot, tmp_path: Path) -> None:
    tiny_encoder, snapshot, _ = tiny_snapshot

    with checkpoint_weights_guarded():
        loaded = tiny_encoder.load(snapshot)
    empty = tmp_path / "no-checkpoint"
    empty.mkdir()

    with pytest.raises(RuntimeError, match="no .safetensors checkpoint"):
        verify_checkpoint_weights(tiny_encoder.TinyEncoder(loaded), empty)


CROSS_PROCESS_SCRIPT = '''
import json, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
import tiny_encoder
from universal_research_mcp.runtime.encoder_loading import (
    checkpoint_weights_guarded, verify_checkpoint_weights,
)

snapshot = sys.argv[3]
with checkpoint_weights_guarded():
    model = tiny_encoder.load(snapshot)
verify_checkpoint_weights(tiny_encoder.TinyEncoder(model), __import__("pathlib").Path(snapshot))
print("RESULT " + json.dumps(tiny_encoder.embed(model)))
'''


def _run_in_subprocess(script: Path, *arguments: str) -> list[float]:
    completed = subprocess.run(
        [sys.executable, str(script), *arguments],
        capture_output=True, text=True, timeout=600, check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    for line in completed.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line.removeprefix("RESULT "))
    raise AssertionError(f"subprocess produced no result: {completed.stdout[-2000:]}")


def test_embeddings_are_reproducible_across_processes(tiny_snapshot, tmp_path: Path) -> None:
    """Catch reinitialization that repeated calls in one process cannot see."""

    tiny_encoder, snapshot, reference = tiny_snapshot
    script = tmp_path / "cross_process.py"
    script.write_text(CROSS_PROCESS_SCRIPT, encoding="utf-8")
    arguments = (str(tmp_path), str(REPO_ROOT), str(snapshot))

    first = _run_in_subprocess(script, *arguments)
    second = _run_in_subprocess(script, *arguments)

    assert first == second
    # Equality across processes alone is satisfied by a fixed random seed, so
    # also require the checkpoint's own values.
    assert first == tiny_encoder.embed(reference)


REAL_SNAPSHOT_SCRIPT = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from universal_research_mcp.semantic_backends import LocalSentenceTransformerEmbedder

embedder = LocalSentenceTransformerEmbedder(
    sys.argv[2], device=sys.argv[3], trust_local_model_code=True,
)
result = embedder.embed(
    ("what is the capital of China?", "北京"),
    model=embedder.model_identity, dimensions=8,
)
print("RESULT " + json.dumps([[round(v, 5) for v in row] for row in result.vectors]))
'''


@pytest.mark.skipif(
    not os.environ.get("URMCP_TEST_LOCAL_SNAPSHOT"),
    reason="set URMCP_TEST_LOCAL_SNAPSHOT to a downloaded snapshot to run the live check",
)
def test_configured_snapshot_embeds_reproducibly_across_processes(tmp_path: Path) -> None:
    script = tmp_path / "real_snapshot.py"
    script.write_text(REAL_SNAPSHOT_SCRIPT, encoding="utf-8")
    snapshot = os.environ["URMCP_TEST_LOCAL_SNAPSHOT"]
    device = os.environ.get("URMCP_TEST_LOCAL_DEVICE", "cpu")

    first = _run_in_subprocess(script, str(REPO_ROOT), snapshot, device)
    second = _run_in_subprocess(script, str(REPO_ROOT), snapshot, device)

    assert first == second
