from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from universal_research_mcp.runtime.model_snapshot import (
    MANIFEST_NAME, SnapshotIdentity, create_snapshot_manifest,
)
from universal_research_mcp.runtime.semantic_config import (
    configure_demo, configure_local, load_semantic_config, write_semantic_config,
)
from universal_research_mcp.semantic_backends import Availability
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


@pytest.fixture
def managed_snapshot(tmp_path: Path):
    _local_embedder.cache_clear()
    model = tmp_path / "offline-model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"fixture-only-model")
    identity = create_snapshot_manifest(model, model_id="fixture/model", revision="a" * 40)
    yield model, identity
    _local_embedder.cache_clear()


def test_managed_model_config_and_runtime_bind_manifest(tmp_path: Path, managed_snapshot) -> None:
    model, identity = managed_snapshot
    report = configure_local(tmp_path, model_path=model, device="cpu")
    assert report["snapshot_verification"] == "verified"
    config = load_semantic_config(tmp_path)
    assert config["backend"]["snapshot"] == identity.to_dict()
    first = configured_backend(tmp_path)
    second = configured_backend(tmp_path)
    assert first.embedder is second.embedder
    assert first.model == f"{model}@sha256:{identity.manifest_sha256}"


def test_changed_manifest_identity_separates_resident_model_and_index_key(
    tmp_path: Path, managed_snapshot,
) -> None:
    model, identity = managed_snapshot
    configure_local(tmp_path, model_path=model, snapshot=identity)
    original = configured_backend(tmp_path)
    # Simulate an explicitly accepted replacement baseline at the same path.
    (model / MANIFEST_NAME).unlink()
    (model / "model.safetensors").write_bytes(b"replacement-fixture")
    replacement = create_snapshot_manifest(model, model_id=identity.model_id, revision=identity.revision)
    configure_local(tmp_path, model_path=model, snapshot=replacement)
    current = configured_backend(tmp_path)
    assert current.embedder is not original.embedder
    assert current.model != original.model


def test_invalid_snapshot_cannot_replace_existing_configuration(tmp_path: Path, managed_snapshot) -> None:
    model, identity = managed_snapshot
    configure_demo(tmp_path)
    config_path = tmp_path / "config/semantic.json"
    before = config_path.read_bytes()
    (model / "config.json").write_text('{"changed": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="files do not match"):
        configure_local(tmp_path, model_path=model, snapshot=identity)
    assert config_path.read_bytes() == before


@pytest.mark.parametrize("change", ["file", "manifest", "missing_manifest"])
def test_cold_backend_refuses_changed_managed_cache(tmp_path: Path, managed_snapshot, change: str) -> None:
    model, identity = managed_snapshot
    configure_local(tmp_path, model_path=model, snapshot=identity)
    if change == "file":
        (model / "model.safetensors").write_bytes(b"modified-fixture")
    elif change == "manifest":
        (model / MANIFEST_NAME).write_bytes((model / MANIFEST_NAME).read_bytes() + b"\n")
    else:
        (model / MANIFEST_NAME).unlink()
    with pytest.raises(ValueError, match="snapshot"):
        configured_backend(tmp_path)


def test_encoder_rechecks_files_changed_after_backend_creation(
    tmp_path: Path, managed_snapshot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, identity = managed_snapshot
    configure_local(tmp_path, model_path=model, snapshot=identity)
    backend = configured_backend(tmp_path)
    monkeypatch.setattr(backend.embedder, "preflight", Availability.ready)
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        SentenceTransformer=lambda *_args, **_kwargs: pytest.fail("unverified model was loaded"),
    ))
    (model / "model.safetensors").write_bytes(b"changed-before-loading")
    with pytest.raises(ValueError, match="files do not match"):
        backend.embedder.embed(("fixture",), model=backend.model, dimensions=2)


def test_verified_encoder_stays_offline_and_is_loaded_once(
    tmp_path: Path, managed_snapshot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, identity = managed_snapshot
    configure_local(tmp_path, model_path=model, snapshot=identity, device="cpu")
    backend = configured_backend(tmp_path)
    monkeypatch.setattr(backend.embedder, "preflight", Availability.ready)
    loads = []

    def fake_encoder(path, **kwargs):
        loads.append((path, kwargs))
        return SimpleNamespace(encode=lambda texts, **_kwargs: [[1.0, 0.0] for _ in texts])

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=fake_encoder))
    for _ in range(2):
        result = backend.embedder.embed(("fixture",), model=backend.model, dimensions=2)
        assert result.model == backend.model
        assert result.vectors == ((1.0, 0.0),)
    assert loads == [(str(model), {"device": "cpu", "local_files_only": True, "trust_remote_code": False})]
    with pytest.raises(ValueError, match="approved local snapshot"):
        backend.embedder.embed(("fixture",), model=str(model), dimensions=2)


def test_legacy_manual_local_config_remains_explicitly_unverified(tmp_path: Path) -> None:
    model = tmp_path / "manual-model"
    write_semantic_config(tmp_path, {
        "schema_version": "semantic-retrieval-config/1.0",
        "backend": {
            "kind": "local_sentence_transformer", "model_path": str(model),
            "device": "cpu", "trust_local_model_code": False, "dimensions": None,
        },
        "auto_refresh": False,
    })
    backend = configured_backend(tmp_path)
    assert backend.model == str(model)
    assert backend.embedder.snapshot is None
    report = configure_local(tmp_path, model_path=model)
    assert report["snapshot_verification"] == "unverified_manual_path"


def test_snapshot_binding_schema_rejects_partial_identity(tmp_path: Path, managed_snapshot) -> None:
    model, identity = managed_snapshot
    configure_local(tmp_path, model_path=model, snapshot=identity)
    config = load_semantic_config(tmp_path)
    del config["backend"]["snapshot"]["manifest_sha256"]
    with pytest.raises(ValueError, match="identity is invalid"):
        write_semantic_config(tmp_path, config)
    with pytest.raises(ValueError, match="SHA-256"):
        SnapshotIdentity.from_dict({**identity.to_dict(), "manifest_sha256": "invalid"})


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
