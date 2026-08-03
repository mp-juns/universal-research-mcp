#!/usr/bin/env python3
"""Build a reproducible local dense-vector index for research-event retrieval.

JSONL remains canonical.  The SQLite database and its manifest can be rebuilt
from the event ledger plus the pinned Hugging Face snapshot recorded in metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.research_device import DEVICE_CHOICES, resolve_torch_device
from scripts.research_event_corrections import (
    apply_source_range_corrections,
    source_range_correction_count,
)
from scripts.research_reference_corpus import REFERENCE_EVENT_TYPE, build_reference_corpus
from core.ledger import validate_records


SCHEMA_VERSION = "1.0"
DEFAULT_MODEL = "Alibaba-NLP/gte-multilingual-base"
ENCODER_DTYPE_CHOICES = ("float32", "float16", "bfloat16")
ENCODER_COMPATIBILITY_BRIDGE_VERSION = "gte-transformers5-strict-reload-rope-v1"
ENCODER_ORACLE_TOLERANCE = 1e-5
ENCODER_ORACLE_TEXTS = (
    "what is the capital of China?",
    "how to implement quick sort in python?",
    "北京",
    "快排算法介绍",
)
ENCODER_ORACLE_EXPECTED = np.asarray(
    [0.301699697971344, 0.7503870129585266, 0.32030850648880005],
    dtype=np.float32,
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def events_and_fingerprint(events_root: Path) -> tuple[list[dict], str]:
    paths = [events_root / "sources.jsonl", *sorted((events_root / "daily").glob("*/events.jsonl"))]
    if not paths[0].exists() or len(paths) == 1:
        raise FileNotFoundError("Expected sources.jsonl and at least one daily events.jsonl")
    digest = hashlib.sha256()
    events: list[dict] = []
    for path in paths:
        digest.update(path.relative_to(events_root).as_posix().encode())
        digest.update(path.read_bytes())
        if path.name == "events.jsonl":
            events.extend(read_jsonl(path))
    validation_issues = validate_records(events)
    if validation_issues:
        rendered = "; ".join(
            f"{issue.record_id}{issue.path}: {issue.message}"
            for issue in validation_issues
        )
        raise ValueError(f"Canonical ledger validation failed: {rendered}")
    ids = [event["event_id"] for event in events]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate event_id in JSONL input")
    events, _ = apply_source_range_corrections(events)
    reference_corpus = build_reference_corpus(events_root.parent)
    events.extend(reference_corpus.events)
    ids = [event["event_id"] for event in events]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate event/reference identifier in retrieval input")
    digest.update(b"reference-corpus-v1")
    digest.update(reference_corpus.fingerprint.encode("ascii"))
    return events, digest.hexdigest()


def event_text(event: dict) -> str:
    """Use concise evidence-bearing fields; avoid embedding entire raw work logs."""
    source = event.get("source", {})
    rows = [
        f"Title: {source.get('heading', event['event_id'])}",
        f"Event type: {event.get('event_type', 'unknown')}",
        f"Status: {event.get('status', 'unknown')}",
        f"Summary: {event.get('summary', '')}",
    ]
    for key in ("expected", "observed", "interpretation", "uncertainty", "next_actions"):
        value = event.get(key)
        if value:
            rows.append(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(rows)


def document_title(lines: list[str], fallback: str) -> str:
    """Return the first Markdown heading as document-level semantic context."""
    for raw in lines:
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", raw)
        if match:
            return match.group(1)
    return fallback


def source_passages(event: dict, project_root: Path, maximum: int = 8, target_chars: int = 1000) -> list[dict]:
    """Create bounded, line-addressable source passages for one event.

    Nested legacy headings may overlap substantially.  Selecting at most four
    evenly distributed paragraphs keeps the derived index compact while giving
    long sections a beginning, middle, and conclusion representation.
    """
    source = event.get("source", {})

    # ARTIFACT_PATH_FALLBACK_V1
    # 기존 line-addressable source_path 형식과 local_artifact의 path 형식을
    # 모두 지원한다. 명시적 줄 범위가 없는 텍스트 artifact는 파일 전체를
    # passage 생성 대상으로 사용한다.
    path_text = source.get("source_path") or source.get("path")

    if not path_text:
        return []

    root = project_root.resolve()
    candidate = Path(path_text)

    if candidate.is_absolute():
        source_path = candidate.resolve()
    else:
        source_path = (root / candidate).resolve()

    # 프로젝트 밖의 파일이나 심볼릭 링크 탈출은 색인하지 않는다.
    try:
        source_path.relative_to(root)
    except ValueError:
        return []

    if not source_path.is_file():
        return []

    raw_start = source.get("line_start")
    raw_end = source.get("line_end")
    is_reference = event.get("event_type") == REFERENCE_EVENT_TYPE
    has_explicit_lines = raw_start is not None and raw_end is not None and not is_reference
    if is_reference:
        maximum = max(maximum, 512)

    # 줄 범위가 없는 artifact는 텍스트 계열 파일만 허용한다.
    artifact_extensions = {
        ".md",
        ".txt",
        ".json",
        ".jsonl",
        ".py",
        ".sh",
        ".bash",
        ".zsh",
        ".yaml",
        ".yml",
        ".toml",
        ".csv",
        ".tsv",
        ".log",
        ".rst",
        ".ini",
        ".cfg",
        ".xml",
        ".html",
    }

    if not has_explicit_lines:
        if source_path.suffix.lower() not in artifact_extensions:
            return []

        # 거대한 산출물을 통째로 임베딩하는 실수를 방지한다.
        if not is_reference and source_path.stat().st_size > 8 * 1024 * 1024:
            return []

    all_lines = source_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    if not all_lines:
        return []

    heading = source.get("heading", event["event_id"])
    root_title = document_title(all_lines, heading)

    relative_parts = source_path.relative_to(root).parts
    is_canonical_daily_jsonl = (
        len(relative_parts) >= 4
        and relative_parts[0] == "research-events"
        and relative_parts[1] == "daily"
        and source_path.name == "events.jsonl"
    )
    if not has_explicit_lines and is_canonical_daily_jsonl:
        for line_number, raw in enumerate(all_lines, start=1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if row.get("event_id") != event["event_id"]:
                continue
            retrieval_text = "\n".join(
                [
                    f"Document title: {event['event_id']}",
                    f"Source path: {path_text}",
                    f"Evidence passage: {event_text(event)}",
                ]
            )
            fingerprint = sha256_bytes(
                f"{event['event_id']}|{path_text}|{line_number}|{line_number}|{retrieval_text}".encode()
            )
            return [
                {
                    "passage_id": f"psg_{fingerprint[:16]}",
                    "event_id": event["event_id"],
                    "source_path": path_text,
                    "source_heading": heading,
                    "line_start": line_number,
                    "line_end": line_number,
                    "text": retrieval_text,
                    "text_sha256": sha256_bytes(retrieval_text.encode()),
                }
            ]
        return []

    if has_explicit_lines:
        start = max(1, int(raw_start))
        end = min(len(all_lines), int(raw_end))
    else:
        start = 1
        end = len(all_lines)

    if start > end:
        return []
    in_code = False
    paragraphs: list[tuple[int, int, str]] = []
    current: list[str] = []
    current_start = start
    for line_number in range(start, end + 1):
        raw = all_lines[line_number - 1]
        if raw.strip().startswith("```"):
            in_code = not in_code
            continue
        cleaned = raw.strip()
        if in_code:
            continue
        cleaned = re.sub(r"^#{1,6}\s+", "", cleaned)
        cleaned = re.sub(r"^[-*]\s+", "", cleaned)
        if not cleaned:
            if current:
                paragraphs.append((current_start, line_number - 1, " ".join(current)))
                current = []
            current_start = line_number + 1
            continue
        # JSON처럼 빈 줄이 거의 없는 파일도 하나의 거대한 passage가
        # 되지 않도록 encoder-friendly 크기로 강제 분할한다.
        if len(cleaned) > target_chars:
            if current:
                paragraphs.append(
                    (
                        current_start,
                        line_number - 1,
                        " ".join(current),
                    )
                )
                current = []

            for offset in range(0, len(cleaned), target_chars):
                paragraphs.append(
                    (
                        line_number,
                        line_number,
                        cleaned[offset : offset + target_chars],
                    )
                )

            current_start = line_number + 1
            continue

        candidate_length = (
            len(cleaned)
            if not current
            else len(" ".join(current)) + len(cleaned) + 1
        )

        if current and candidate_length > target_chars:
            paragraphs.append(
                (
                    current_start,
                    line_number - 1,
                    " ".join(current),
                )
            )
            current = []

        if not current:
            current_start = line_number

        current.append(cleaned)
    if current:
        paragraphs.append((current_start, end, " ".join(current)))
    chunks: list[tuple[int, int, str]] = []
    chunk_lines: list[str] = []
    chunk_start = start
    chunk_end = start
    for paragraph_start, paragraph_end, text in paragraphs:
        if chunk_lines and len(" ".join(chunk_lines)) + len(text) + 1 > target_chars:
            chunks.append((chunk_start, chunk_end, " ".join(chunk_lines)))
            chunk_lines = []
        if not chunk_lines:
            chunk_start = paragraph_start
        chunk_lines.append(text)
        chunk_end = paragraph_end
    if chunk_lines:
        chunks.append((chunk_start, chunk_end, " ".join(chunk_lines)))
    if source_path.suffix.lower() in {".json", ".jsonl"}:
        # Structured audit JSON에서는 앞·중간·끝 균등 샘플링이
        # selected metric, quality gate, decision을 임의로 탈락시킨다.
        # 일반적인 audit 크기는 전부 보존하고 비정상적으로 큰 경우만
        # 128개까지 균등 축소한다.
        json_maximum = 128

        if len(chunks) > json_maximum:
            positions = {
                round(
                    index
                    * (len(chunks) - 1)
                    / (json_maximum - 1)
                )
                for index in range(json_maximum)
            }
            chunks = [
                chunk
                for index, chunk in enumerate(chunks)
                if index in positions
            ]

    elif len(chunks) > maximum:
        positions = {
            round(
                index
                * (len(chunks) - 1)
                / (maximum - 1)
            )
            for index in range(maximum)
        }
        chunks = [
            chunk
            for index, chunk in enumerate(chunks)
            if index in positions
        ]
    records = []
    for line_start, line_end, text in chunks:
        if not text:
            continue
        context_rows = [
            f"Document title: {root_title}",
        ]
        if heading != root_title:
            context_rows.append(f"Section heading: {heading}")
        context_rows.extend(
            [
                f"Source path: {path_text}",
                f"Evidence passage: {text}",
            ]
        )
        retrieval_text = "\n".join(context_rows)
        fingerprint = sha256_bytes(
            f"{event['event_id']}|{path_text}|{line_start}|{line_end}|{retrieval_text}".encode()
        )
        records.append(
            {
                "passage_id": f"psg_{fingerprint[:16]}",
                "event_id": event["event_id"],
                "source_path": path_text,
                "source_heading": heading,
                "line_start": line_start,
                "line_end": line_end,
                "text": retrieval_text,
                "text_sha256": sha256_bytes(retrieval_text.encode()),
            }
        )
    return records


def configure_hf_modules_cache(path: Path) -> Path:
    """Keep dynamic-module imports out of the user home and inside derived artifacts."""
    path.mkdir(parents=True, exist_ok=True)
    os.environ["HF_MODULES_CACHE"] = str(path)
    # Some Transformers versions bind this path at import time. Override the
    # module constant too, so subprocesses never fall back to the user home.
    from transformers import dynamic_module_utils

    dynamic_module_utils.HF_MODULES_CACHE = str(path)
    return path


def snapshot_model(model: str, revision: str | None, allow_download: bool) -> tuple[Path, str]:
    from huggingface_hub import snapshot_download

    snapshot = Path(
        snapshot_download(
            repo_id=model,
            revision=revision,
            local_files_only=not allow_download,
        )
    )
    resolved_revision = snapshot.name
    return snapshot, resolved_revision


def snapshot_hashes(snapshot: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(candidate for candidate in snapshot.rglob("*") if candidate.is_file()):
        hashes[path.relative_to(snapshot).as_posix()] = sha256_file(path)
    return hashes


def remote_code_dependencies(snapshot: Path) -> list[dict[str, object]]:
    """Bind any Hugging Face ``repo--module`` auto-map dependencies already cached."""
    from huggingface_hub import snapshot_download

    config = json.loads((snapshot / "config.json").read_text(encoding="utf-8"))
    repositories = sorted(
        {
            value.split("--", 1)[0]
            for value in config.get("auto_map", {}).values()
            if "--" in value
        }
    )
    dependencies = []
    for repository in repositories:
        cached_snapshot = Path(snapshot_download(repository, local_files_only=True))
        dependencies.append(
            {
                "repository": repository,
                "revision": cached_snapshot.name,
                "snapshot_path": str(cached_snapshot),
                "file_sha256": snapshot_hashes(cached_snapshot),
            }
        )
    return dependencies


def remote_code_kwargs(snapshot: Path, allow_download: bool) -> dict[str, str]:
    """Return kwargs that bind the pinned remote-code revision during offline load."""
    from huggingface_hub import snapshot_download

    config = json.loads((snapshot / "config.json").read_text(encoding="utf-8"))
    repositories = sorted(
        {
            value.split("--", 1)[0]
            for value in config.get("auto_map", {}).values()
            if "--" in value
        }
    )
    if not repositories:
        return {}
    if len(repositories) > 1:
        raise ValueError(f"Expected at most one remote-code repository, received {repositories}")
    dependency_snapshot = Path(
        snapshot_download(repositories[0], local_files_only=not allow_download)
    )
    return {"code_revision": dependency_snapshot.name}


def resolve_encoder_dtype(name: str):
    if name not in ENCODER_DTYPE_CHOICES:
        raise ValueError(
            f"encoder dtype must be one of {ENCODER_DTYPE_CHOICES}, received {name!r}"
        )
    import torch

    return getattr(torch, name)


def sentence_transformer_base_model(model):
    """Return the pinned GTE AutoModel contained in SentenceTransformer."""
    try:
        transformer = model[0]
    except Exception as exc:
        raise ValueError("Encoder compatibility bridge requires transformer module 0") from exc
    base_model = getattr(transformer, "auto_model", None)
    if base_model is None:
        raise ValueError("Encoder compatibility bridge could not find module 0 auto_model")
    return base_model


def checkpoint_bridge_layout(snapshot: Path) -> tuple[list[str], list[str]]:
    """Validate the pinned checkpoint namespace without materializing tensors."""
    from safetensors import safe_open

    checkpoint = snapshot / "model.safetensors"
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Pinned encoder checkpoint is absent: {checkpoint}")
    with safe_open(checkpoint, framework="pt", device="cpu") as handle:
        keys = sorted(handle.keys())
    mapped = sorted(key[4:] for key in keys if key.startswith("new."))
    excluded = sorted(key for key in keys if not key.startswith("new."))
    expected_excluded = ["classifier.bias", "classifier.weight"]
    if not mapped:
        raise ValueError("Pinned encoder checkpoint has no new.* base tensors")
    if excluded != expected_excluded:
        raise ValueError(
            "Pinned encoder checkpoint exclusions changed: "
            f"expected {expected_excluded}, received {excluded}"
        )
    return mapped, excluded


def restore_encoder_checkpoint_weights(model, snapshot: Path) -> int:
    """Undo Transformers 5.x reinitialization of the pinned remote model.

    Transformers 5.1 finalizes this older remote-code model by calling its
    unguarded ``_init_weights`` after checkpoint tensors were installed.  Load
    the locally pinned tensors again only after construction has completed.
    """
    from safetensors.torch import load_file

    mapped_names, _ = checkpoint_bridge_layout(snapshot)
    base_model = sentence_transformer_base_model(model)
    base_names = sorted(base_model.state_dict().keys())
    if mapped_names != base_names:
        missing = sorted(set(base_names) - set(mapped_names))
        unexpected = sorted(set(mapped_names) - set(base_names))
        raise ValueError(
            "Pinned encoder checkpoint/base-model keys differ: "
            f"missing={missing}, unexpected={unexpected}"
        )

    checkpoint_state = load_file(str(snapshot / "model.safetensors"), device="cpu")
    mapped_state = {
        key[4:]: value for key, value in checkpoint_state.items() if key.startswith("new.")
    }
    base_model.load_state_dict(mapped_state, strict=True)
    return len(mapped_state)


def repair_encoder_nonpersistent_buffers(model) -> int:
    """Rebuild GTE position and RoPE buffers destroyed by Transformers 5.1."""
    import torch

    base_model = sentence_transformer_base_model(model)
    embeddings = getattr(base_model, "embeddings", None)
    config = getattr(base_model, "config", None)
    if embeddings is None or config is None:
        raise ValueError("Pinned encoder base model lacks embeddings/config")
    if not hasattr(embeddings, "_init_rope") or not hasattr(embeddings, "word_embeddings"):
        raise ValueError("Pinned encoder embeddings lack deterministic RoPE initialization")

    device = embeddings.word_embeddings.weight.device
    position_ids = torch.arange(int(config.max_position_embeddings), device=device)
    embeddings.register_buffer("position_ids", position_ids, persistent=False)
    embeddings._init_rope(config)
    embeddings.rotary_emb.to(device=device)

    buffers = dict(base_model.named_buffers())
    expected_names = {
        "embeddings.position_ids",
        "embeddings.rotary_emb.inv_freq",
        "embeddings.rotary_emb.cos_cached",
        "embeddings.rotary_emb.sin_cached",
    }
    missing = sorted(expected_names - buffers.keys())
    if missing:
        raise ValueError(f"Encoder compatibility bridge did not rebuild buffers: {missing}")
    for name in expected_names:
        value = buffers[name]
        if value.is_floating_point() and not torch.isfinite(value).all():
            raise ValueError(f"Encoder compatibility bridge produced non-finite buffer {name}")

    if not torch.equal(buffers["embeddings.position_ids"], position_ids):
        raise ValueError("Encoder position_ids reconstruction mismatch")
    cosine_zero = buffers["embeddings.rotary_emb.cos_cached"][0]
    sine_zero = buffers["embeddings.rotary_emb.sin_cached"][0]
    if not torch.equal(cosine_zero, torch.ones_like(cosine_zero)):
        raise ValueError("Encoder RoPE cosine zero-position invariant failed")
    if not torch.equal(sine_zero, torch.zeros_like(sine_zero)):
        raise ValueError("Encoder RoPE sine zero-position invariant failed")
    return len(expected_names)


def validate_encoder_model_card_oracle(model) -> float:
    """Verify that the repaired encoder reproduces the pinned model-card scores."""
    vectors = model.encode(
        list(ENCODER_ORACLE_TEXTS),
        batch_size=len(ENCODER_ORACLE_TEXTS),
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype(np.float32)
    scores = (vectors[:1] @ vectors[1:].T).reshape(-1)
    if scores.shape != ENCODER_ORACLE_EXPECTED.shape or not np.allclose(
        scores,
        ENCODER_ORACLE_EXPECTED,
        rtol=ENCODER_ORACLE_TOLERANCE,
        atol=ENCODER_ORACLE_TOLERANCE,
    ):
        raise ValueError(
            "Encoder model-card oracle failed: "
            f"expected {ENCODER_ORACLE_EXPECTED.tolist()}, received {scores.tolist()}"
        )
    return float(np.max(np.abs(scores - ENCODER_ORACLE_EXPECTED)))


def apply_encoder_compatibility_bridge(model, snapshot: Path) -> dict[str, object]:
    restored_tensor_count = restore_encoder_checkpoint_weights(model, snapshot)
    repaired_buffer_count = repair_encoder_nonpersistent_buffers(model)
    oracle_max_abs_delta = validate_encoder_model_card_oracle(model)
    return {
        "version": ENCODER_COMPATIBILITY_BRIDGE_VERSION,
        "restored_tensor_count": restored_tensor_count,
        "repaired_buffer_count": repaired_buffer_count,
        "oracle_max_abs_delta": oracle_max_abs_delta,
    }


def select_encoder_smoke_texts(texts: list[str]) -> list[str]:
    """Select deterministic shortest/median/longest texts for a live finite gate."""
    if not texts:
        raise ValueError("Cannot encode an empty research corpus")
    ordered = sorted(enumerate(texts), key=lambda item: (len(item[1]), item[0]))
    positions = (0, len(ordered) // 2, len(ordered) - 1)
    selected: list[str] = []
    selected_indexes: set[int] = set()
    for position in positions:
        original_index, text = ordered[position]
        if original_index not in selected_indexes:
            selected.append(text)
            selected_indexes.add(original_index)
    return selected


def encode_texts(
    texts: Iterable[str], snapshot: Path, device: str, batch_size: int, max_length: int, dimensions: int,
    model_kwargs: dict[str, str] | None = None,
    encoder_dtype: str = "float32",
) -> np.ndarray:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - exercised by CLI environments
        raise RuntimeError("sentence-transformers is required; install the pinned package first") from exc

    requested_dtype = resolve_encoder_dtype(encoder_dtype)
    model = SentenceTransformer(
        str(snapshot), trust_remote_code=True, local_files_only=True, device=device,
        config_kwargs=model_kwargs or {},
        model_kwargs={**(model_kwargs or {}), "torch_dtype": requested_dtype},
    )

    loaded_dtype = next(model.parameters()).dtype
    if loaded_dtype != requested_dtype:
        raise ValueError(
            f"Encoder parameter dtype mismatch: requested {requested_dtype}, loaded {loaded_dtype}"
        )

    model.max_seq_length = max_length
    apply_encoder_compatibility_bridge(model, snapshot)
    materialized_texts = list(texts)
    smoke_texts = select_encoder_smoke_texts(materialized_texts)
    smoke_vectors = model.encode(
        smoke_texts,
        batch_size=1,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=False,
    ).astype(np.float32)
    normalize_vectors(smoke_vectors, dimensions)
    vectors = model.encode(
        materialized_texts,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=False,
    ).astype(np.float32)
    return normalize_vectors(vectors, dimensions)


def normalize_vectors(vectors: np.ndarray, dimensions: int) -> np.ndarray:
    if vectors.ndim != 2:
        raise ValueError(f"Encoder output must be rank 2, received shape {vectors.shape}")
    if dimensions < 1 or dimensions > vectors.shape[1]:
        raise ValueError(f"--dimensions must be in [1, {vectors.shape[1]}], received {dimensions}")
    vectors = vectors[:, :dimensions]
    if not np.isfinite(vectors).all():
        bad_rows = int(np.count_nonzero(~np.isfinite(vectors).all(axis=1)))
        raise ValueError(f"Encoder produced non-finite vectors in {bad_rows} rows")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if not np.isfinite(norms).all():
        raise ValueError("Encoder produced non-finite vector norms")
    if np.any(norms == 0):
        raise ValueError("Encoder produced a zero-norm vector")
    normalized = vectors / norms
    if not np.isfinite(normalized).all():
        raise ValueError("Encoder normalization produced non-finite vectors")
    return normalized


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = DELETE;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE embeddings (
            event_id TEXT PRIMARY KEY,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            retrieval_text_sha256 TEXT NOT NULL
        );
        CREATE TABLE passage_embeddings (
            passage_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_heading TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            retrieval_text_sha256 TEXT NOT NULL
        );
        CREATE INDEX passage_event_idx ON passage_embeddings(event_id);
        """
    )


def build(
    events_root: Path,
    output: Path,
    manifest_path: Path,
    model: str,
    revision: str | None,
    device: str,
    max_length: int,
    dimensions: int,
    batch_size: int,
    allow_download: bool,
    encoder_dtype: str = "float32",
) -> dict:
    if max_length < 1 or batch_size < 1:
        raise ValueError("--max-length and --batch-size must be positive")
    events, source_fingerprint = events_and_fingerprint(events_root)
    reference_count = sum(event.get("event_type") == REFERENCE_EVENT_TYPE for event in events)
    canonical_event_count = len(events) - reference_count
    reference_pdf_count = sum(
        bool(event.get("reference_extraction", {}).get("pdf")) for event in events
    )
    reference_pdf_page_count = sum(
        int(event.get("reference_extraction", {}).get("page_count", 0)) for event in events
    )
    reference_sparse_pdf_page_count = sum(
        len(event.get("reference_extraction", {}).get("sparse_pages", [])) for event in events
    )
    reference_ocr_pdf_page_count = sum(
        len(event.get("reference_extraction", {}).get("ocr_pages", [])) for event in events
    )
    snapshot, resolved_revision = snapshot_model(model, revision, allow_download)
    selected_device = resolve_torch_device(device)
    modules_cache = configure_hf_modules_cache(output.parent / "hf-modules")
    encoder_kwargs = remote_code_kwargs(snapshot, allow_download)
    texts = [event_text(event) for event in events]
    passages = [
        passage for event in events for passage in source_passages(event, events_root.parent)
    ]
    all_texts = [*texts, *(passage["text"] for passage in passages)]
    encoder_smoke_count = len(select_encoder_smoke_texts(all_texts))
    vectors = encode_texts(
        all_texts,
        snapshot, selected_device, batch_size, max_length, dimensions, encoder_kwargs,
        encoder_dtype,
    )
    event_vectors = vectors[: len(events)]
    passage_vectors = vectors[len(events) :]

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with sqlite3.connect(output) as connection:
        initialize(connection)
        connection.executemany(
            "INSERT INTO embeddings VALUES (?, ?, ?, ?)",
            [
                (event["event_id"], int(event_vectors.shape[1]), vector.tobytes(), sha256_bytes(text.encode()))
                for event, text, vector in zip(events, texts, event_vectors, strict=True)
            ],
        )
        connection.executemany(
            "INSERT INTO passage_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    passage["passage_id"], passage["event_id"], passage["source_path"], passage["source_heading"],
                    passage["line_start"], passage["line_end"], int(passage_vectors.shape[1]), vector.tobytes(),
                    passage["text_sha256"],
                )
                for passage, vector in zip(passages, passage_vectors, strict=True)
            ],
        )
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "index_kind": "local_dense_cosine_research_memory",
            "event_count": str(canonical_event_count),
            "reference_count": str(reference_count),
            "embedding_count": str(len(events)),
            "reference_pdf_count": str(reference_pdf_count),
            "reference_pdf_page_count": str(reference_pdf_page_count),
            "reference_sparse_pdf_page_count": str(reference_sparse_pdf_page_count),
            "reference_ocr_pdf_page_count": str(reference_ocr_pdf_page_count),
            "dimensions": str(event_vectors.shape[1]),
            "passage_count": str(len(passages)),
            "source_bundle_sha256": source_fingerprint,
            "source_range_correction_count": str(
                source_range_correction_count(events)
            ),
            "model": model,
            "model_revision": resolved_revision,
            "snapshot_path": str(snapshot),
            "device": selected_device,
            "encoder_dtype": encoder_dtype,
            "encoder_smoke_count": str(encoder_smoke_count),
            "encoder_compatibility_bridge": ENCODER_COMPATIBILITY_BRIDGE_VERSION,
            "encoder_restored_tensor_count": str(len(checkpoint_bridge_layout(snapshot)[0])),
            "encoder_repaired_buffer_count": "4",
            "encoder_oracle_status": "passed",
            "encoder_oracle_tolerance": str(ENCODER_ORACLE_TOLERANCE),
            "max_length": str(max_length),
            "hf_modules_cache": str(modules_cache),
        }
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        connection.commit()
    connection.close()

    manifest = {
        **metadata,
        "model_file_sha256": snapshot_hashes(snapshot),
        "remote_code_dependencies": remote_code_dependencies(snapshot),
        "python_platform": platform.platform(),
        "notes": [
            "JSONL is canonical; this database is a rebuildable derived index.",
            "Reference records are derived from references/manifest.json and do not become canonical research events.",
            "Semantic similarity returns candidates only; retrieve source lines before making a research claim.",
            "The model requires trust_remote_code=True; snapshot hashes bind the executed local files.",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output": str(output), "manifest": str(manifest_path), **metadata}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision")
    parser.add_argument("--device", choices=DEVICE_CHOICES, default="auto")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--dimensions", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument(
        "--encoder-dtype", choices=ENCODER_DTYPE_CHOICES, default="float32"
    )
    parser.add_argument("--allow-download", action="store_true")
    args = parser.parse_args()
    report = build(
        args.events_root.resolve(), args.output.resolve(), args.manifest.resolve(), args.model, args.revision,
        args.device, args.max_length, args.dimensions, args.batch_size, args.allow_download,
        args.encoder_dtype,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
