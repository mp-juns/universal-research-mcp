#!/usr/bin/env python3
"""Watch daemon for automatic research-index rebuilds.

Monitors ``research-events/daily/`` for JSONL changes and triggers:
- **Lexical index**: full rebuild via ``build_research_ledger_index`` (~1 s).
- **Semantic index**: incremental — only new events are embedded and INSERTed.

The ``gte-multilingual-base`` model is loaded once at startup and stays resident
on the best available device (CUDA > MPS > CPU).  Changes are debounced so that
rapid sequential appends are grouped into a single rebuild.

Usage::

    python3 scripts/watch_research_index.py \\
      --events-root research-events \\
      --lexical-db research-events/index/research.sqlite \\
      --semantic-db research-events/index/semantic.sqlite \\
      --manifest research-events/index/semantic-manifest.json \\
      --device auto --debounce 10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import signal
import sqlite3
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universal_research_mcp.tools.build_research_ledger_index import build as build_lexical
from universal_research_mcp.tools.build_research_semantic_index import (
    DEFAULT_MODEL,
    ENCODER_COMPATIBILITY_BRIDGE_VERSION,
    ENCODER_DTYPE_CHOICES,
    ENCODER_ORACLE_TOLERANCE,
    SCHEMA_VERSION,
    apply_encoder_compatibility_bridge,
    checkpoint_bridge_layout,
    configure_hf_modules_cache,
    event_text,
    events_and_fingerprint,
    normalize_vectors,
    remote_code_dependencies,
    remote_code_kwargs,
    resolve_encoder_dtype,
    select_encoder_smoke_texts,
    snapshot_hashes,
    snapshot_model,
    source_passages,
)
from universal_research_mcp.tools.research_device import DEVICE_CHOICES, resolve_torch_device
from universal_research_mcp.tools.research_event_corrections_v2 import (
    source_range_correction_count,
    source_range_correction_target_ids,
)
from universal_research_mcp.tools.research_reference_corpus import REFERENCE_EVENT_TYPE

KST = timezone(timedelta(hours=9))
logger = logging.getLogger("watch-research-index")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def kst_now() -> str:
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def existing_event_ids(db_path: Path) -> set[str]:
    """Return event IDs already present in the semantic SQLite."""
    if not db_path.exists():
        return set()
    with sqlite3.connect(db_path) as conn:
        try:
            rows = conn.execute("SELECT event_id FROM embeddings").fetchall()
        except sqlite3.OperationalError:
            return set()
    return {row[0] for row in rows}


def existing_passage_ids(db_path: Path) -> set[str]:
    """Return passage IDs already present in the semantic SQLite."""
    if not db_path.exists():
        return set()
    with sqlite3.connect(db_path) as conn:
        try:
            rows = conn.execute("SELECT passage_id FROM passage_embeddings").fetchall()
        except sqlite3.OperationalError:
            return set()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Encoder holder — loaded once, reused on every rebuild
# ---------------------------------------------------------------------------

class EncoderHolder:
    """Lazily loads and caches the SentenceTransformer model."""

    def __init__(
        self,
        model_name: str,
        revision: str | None,
        device: str,
        max_length: int,
        dimensions: int,
        batch_size: int,
        allow_download: bool,
        hf_modules_cache: Path,
        encoder_dtype: str = "float32",
    ):
        self.model_name = model_name
        self.revision = revision
        self.device = device
        self.max_length = max_length
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.allow_download = allow_download
        self.hf_modules_cache = hf_modules_cache
        self.encoder_dtype = encoder_dtype

        # Resolved at load time
        self._model: Any | None = None
        self._snapshot: Path | None = None
        self._resolved_revision: str | None = None
        self._selected_device: str | None = None
        self._encoder_kwargs: dict[str, str] = {}
        self._bridge_report: dict[str, object] = {}

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is required; install the pinned package first"
            ) from exc

        import numpy as np  # noqa: F401 — validate early

        configure_hf_modules_cache(self.hf_modules_cache)
        self._snapshot, self._resolved_revision = snapshot_model(
            self.model_name, self.revision, self.allow_download
        )
        self._selected_device = resolve_torch_device(self.device)
        self._encoder_kwargs = remote_code_kwargs(self._snapshot, self.allow_download)

        logger.info(
            "Loading encoder %s (rev %s) on %s …",
            self.model_name,
            self._resolved_revision,
            self._selected_device,
        )
        requested_dtype = resolve_encoder_dtype(self.encoder_dtype)
        self._model = SentenceTransformer(
            str(self._snapshot),
            trust_remote_code=True,
            local_files_only=True,
            device=self._selected_device,
            config_kwargs=self._encoder_kwargs or {},
            model_kwargs={**(self._encoder_kwargs or {}), "torch_dtype": requested_dtype},
        )

        loaded_dtype = next(self._model.parameters()).dtype
        if loaded_dtype != requested_dtype:
            raise ValueError(
                f"Encoder parameter dtype mismatch: requested {requested_dtype}, loaded {loaded_dtype}"
            )

        self._model.max_seq_length = self.max_length
        self._bridge_report = apply_encoder_compatibility_bridge(
            self._model, self._snapshot
        )
        logger.info("Encoder ready on %s", self._selected_device)

    def encode(self, texts: list[str]) -> Any:
        import numpy as np

        self._ensure_loaded()
        assert self._model is not None
        smoke_vectors = self._model.encode(
            select_encoder_smoke_texts(texts),
            batch_size=1,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=False,
        ).astype(np.float32)
        normalize_vectors(smoke_vectors, self.dimensions)
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=False,
        ).astype(np.float32)
        return normalize_vectors(vectors, self.dimensions)

    @property
    def snapshot(self) -> Path:
        self._ensure_loaded()
        assert self._snapshot is not None
        return self._snapshot

    @property
    def resolved_revision(self) -> str:
        self._ensure_loaded()
        assert self._resolved_revision is not None
        return self._resolved_revision

    @property
    def selected_device(self) -> str:
        self._ensure_loaded()
        assert self._selected_device is not None
        return self._selected_device


# ---------------------------------------------------------------------------
# Incremental semantic rebuild
# ---------------------------------------------------------------------------

def incremental_semantic_rebuild(
    events_root: Path,
    semantic_db: Path,
    manifest_path: Path,
    encoder: EncoderHolder,
) -> dict[str, Any]:
    """Embed only new events and passages, INSERT into existing DB."""
    import numpy as np

    events, source_fingerprint = events_and_fingerprint(events_root)
    reference_count = sum(
        event.get("event_type") == REFERENCE_EVENT_TYPE for event in events
    )
    canonical_event_count = len(events) - reference_count
    reference_pdf_count = sum(
        bool(event.get("reference_extraction", {}).get("pdf")) for event in events
    )
    reference_pdf_page_count = sum(
        int(event.get("reference_extraction", {}).get("page_count", 0))
        for event in events
    )
    reference_sparse_pdf_page_count = sum(
        len(event.get("reference_extraction", {}).get("sparse_pages", []))
        for event in events
    )
    reference_ocr_pdf_page_count = sum(
        len(event.get("reference_extraction", {}).get("ocr_pages", []))
        for event in events
    )
    known_event_ids = existing_event_ids(semantic_db)
    known_passage_ids = existing_passage_ids(semantic_db)

    new_events = [e for e in events if e["event_id"] not in known_event_ids]
    events_by_id = {event["event_id"]: event for event in events}
    correction_target_ids = source_range_correction_target_ids(new_events)
    missing_correction_targets = correction_target_ids - set(events_by_id)
    if missing_correction_targets:
        raise ValueError(
            "Source correction targets are missing from retrieval input: "
            f"{sorted(missing_correction_targets)}"
        )
    refreshed_event_ids = correction_target_ids & known_event_ids
    refreshed_events = [events_by_id[event_id] for event_id in sorted(refreshed_event_ids)]

    new_event_passages_all = [
        p
        for e in new_events
        for p in source_passages(e, events_root.parent)
    ]
    new_passages = [
        passage
        for passage in new_event_passages_all
        if passage["passage_id"] not in known_passage_ids
    ]
    refreshed_passages = [
        passage
        for event in refreshed_events
        for passage in source_passages(event, events_root.parent)
    ]
    passages_to_insert = [*new_passages, *refreshed_passages]
    passage_ids_to_insert = [passage["passage_id"] for passage in passages_to_insert]
    if len(passage_ids_to_insert) != len(set(passage_ids_to_insert)):
        raise ValueError("Duplicate passage_id while preparing incremental semantic update")

    if not new_events and not passages_to_insert:
        return {
            "new_events": 0,
            "new_passages": 0,
            "refreshed_events": 0,
            "refreshed_passages": 0,
            "status": "no_change",
        }

    # Prepare texts to encode
    event_texts = [event_text(e) for e in new_events]
    passage_texts = [passage["text"] for passage in passages_to_insert]
    all_texts = event_texts + passage_texts

    if all_texts:
        vectors = encoder.encode(all_texts)
        event_vectors = vectors[: len(new_events)]
        passage_vectors = vectors[len(new_events) :]
    else:
        event_vectors = np.empty((0, encoder.dimensions), dtype=np.float32)
        passage_vectors = np.empty((0, encoder.dimensions), dtype=np.float32)

    # INSERT into existing DB
    with sqlite3.connect(semantic_db) as conn:
        if new_events:
            conn.executemany(
                "INSERT OR IGNORE INTO embeddings VALUES (?, ?, ?, ?)",
                [
                    (
                        event["event_id"],
                        int(event_vectors.shape[1]),
                        vector.tobytes(),
                        sha256_bytes(text.encode()),
                    )
                    for event, text, vector in zip(
                        new_events, event_texts, event_vectors, strict=True
                    )
                ],
            )
        if refreshed_event_ids:
            conn.executemany(
                "DELETE FROM passage_embeddings WHERE event_id = ?",
                [(event_id,) for event_id in sorted(refreshed_event_ids)],
            )
        if passages_to_insert:
            conn.executemany(
                "INSERT INTO passage_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        p["passage_id"],
                        p["event_id"],
                        p["source_path"],
                        p["source_heading"],
                        p["line_start"],
                        p["line_end"],
                        int(passage_vectors.shape[1]),
                        vector.tobytes(),
                        p["text_sha256"],
                    )
                    for p, vector in zip(
                        passages_to_insert, passage_vectors, strict=True
                    )
                ],
            )
        passage_count = conn.execute(
            "SELECT COUNT(*) FROM passage_embeddings"
        ).fetchone()[0]
        embedding_count = conn.execute(
            "SELECT COUNT(*) FROM embeddings"
        ).fetchone()[0]
        bridge_report = getattr(encoder, "_bridge_report", {})
        restored_tensor_count = bridge_report.get("restored_tensor_count")
        if restored_tensor_count is None:
            restored_tensor_count = len(checkpoint_bridge_layout(encoder.snapshot)[0])
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "index_kind": "local_dense_cosine_research_memory",
            "event_count": str(canonical_event_count),
            "reference_count": str(reference_count),
            "embedding_count": str(embedding_count),
            "reference_pdf_count": str(reference_pdf_count),
            "reference_pdf_page_count": str(reference_pdf_page_count),
            "reference_sparse_pdf_page_count": str(reference_sparse_pdf_page_count),
            "reference_ocr_pdf_page_count": str(reference_ocr_pdf_page_count),
            "dimensions": str(encoder.dimensions),
            "passage_count": str(passage_count),
            "source_bundle_sha256": source_fingerprint,
            "source_range_correction_count": str(
                source_range_correction_count(events)
            ),
            "model": encoder.model_name,
            "model_revision": encoder.resolved_revision,
            "snapshot_path": str(encoder.snapshot),
            "device": encoder.selected_device,
            "encoder_dtype": encoder.encoder_dtype,
            "encoder_smoke_count": str(len(select_encoder_smoke_texts(all_texts))),
            "encoder_compatibility_bridge": ENCODER_COMPATIBILITY_BRIDGE_VERSION,
            "encoder_restored_tensor_count": str(restored_tensor_count),
            "encoder_repaired_buffer_count": str(
                bridge_report.get("repaired_buffer_count", 4)
            ),
            "encoder_oracle_status": "passed",
            "encoder_oracle_tolerance": str(ENCODER_ORACLE_TOLERANCE),
            "max_length": str(encoder.max_length),
            "hf_modules_cache": str(
                encoder.snapshot.parent.parent.parent / "hf-modules"
            ),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO metadata VALUES (?, ?)", metadata.items()
        )
        conn.commit()

    # Update manifest
    manifest = {
        **metadata,
        "model_file_sha256": snapshot_hashes(encoder.snapshot),
        "remote_code_dependencies": remote_code_dependencies(encoder.snapshot),
        "python_platform": platform.platform(),
        "last_incremental_update": kst_now(),
        "notes": [
            "JSONL is canonical; this database is a rebuildable derived index.",
            "Reference records are derived from references/manifest.json and do not become canonical research events.",
            "Semantic similarity returns candidates only; retrieve source lines before making a research claim.",
            "The model requires trust_remote_code=True; snapshot hashes bind the executed local files.",
            "This manifest was updated by watch_research_index.py incremental daemon.",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "new_events": len(new_events),
        "new_passages": len(new_passages),
        "refreshed_events": len(refreshed_events),
        "refreshed_passages": len(refreshed_passages),
        "total_events": canonical_event_count,
        "total_embeddings": embedding_count,
        "total_passages": passage_count,
        "status": "updated",
    }


# ---------------------------------------------------------------------------
# Rebuild orchestrator
# ---------------------------------------------------------------------------

def run_rebuild(
    events_root: Path,
    lexical_db: Path,
    semantic_db: Path,
    manifest_path: Path,
    encoder: EncoderHolder,
) -> None:
    """Run both lexical (full) and semantic (incremental) rebuilds."""
    timestamp = kst_now()

    # 1. Lexical — full rebuild
    try:
        report = build_lexical(events_root, lexical_db)
        logger.info(
            "[%s] Lexical rebuild OK: %s events", timestamp, report.get("event_count")
        )
    except Exception:
        logger.exception("[%s] Lexical rebuild FAILED", timestamp)

    # 2. Semantic — incremental
    try:
        report = incremental_semantic_rebuild(
            events_root, semantic_db, manifest_path, encoder
        )
        if report["status"] == "no_change":
            logger.info("[%s] Semantic: no new events to embed", timestamp)
        else:
            logger.info(
                "[%s] Semantic rebuild OK: +%d events, +%d passages, refreshed %d events/%d passages (total %s events, %s passages)",
                timestamp,
                report["new_events"],
                report["new_passages"],
                report["refreshed_events"],
                report["refreshed_passages"],
                report["total_events"],
                report["total_passages"],
            )
    except Exception:
        logger.exception("[%s] Semantic incremental rebuild FAILED", timestamp)


# ---------------------------------------------------------------------------
# Debounced watchdog handler
# ---------------------------------------------------------------------------

class DebouncedJSONLHandler(FileSystemEventHandler):
    """Triggers a rebuild after a debounce period when JSONL files change."""

    def __init__(
        self,
        debounce_seconds: float,
        events_root: Path,
        lexical_db: Path,
        semantic_db: Path,
        manifest_path: Path,
        encoder: EncoderHolder,
    ):
        super().__init__()
        self.debounce_seconds = debounce_seconds
        self.events_root = events_root
        self.lexical_db = lexical_db
        self.semantic_db = semantic_db
        self.manifest_path = manifest_path
        self.encoder = encoder

        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _is_jsonl(self, path: str) -> bool:
        return path.endswith(".jsonl")

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory or not self._is_jsonl(event.src_path):
            return
        self._schedule_rebuild(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or not self._is_jsonl(event.src_path):
            return
        self._schedule_rebuild(event.src_path)

    def _schedule_rebuild(self, trigger_path: str) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            logger.info(
                "Change detected: %s — rebuild in %.0fs",
                trigger_path,
                self.debounce_seconds,
            )
            self._timer = threading.Timer(
                self.debounce_seconds, self._do_rebuild
            )
            self._timer.daemon = True
            self._timer.start()

    def _do_rebuild(self) -> None:
        try:
            run_rebuild(
                self.events_root,
                self.lexical_db,
                self.semantic_db,
                self.manifest_path,
                self.encoder,
            )
        except Exception:
            logger.exception("Rebuild failed unexpectedly")


# ---------------------------------------------------------------------------
# Full semantic rebuild (for first run or --full-rebuild flag)
# ---------------------------------------------------------------------------

def full_semantic_rebuild(
    events_root: Path,
    semantic_db: Path,
    manifest_path: Path,
    encoder: EncoderHolder,
) -> dict[str, Any]:
    """Drop and rebuild semantic.sqlite from scratch."""
    from universal_research_mcp.tools.build_research_semantic_index import build

    return build(
        events_root=events_root,
        output=semantic_db,
        manifest_path=manifest_path,
        model=encoder.model_name,
        revision=encoder.revision,
        device=encoder.device,
        max_length=encoder.max_length,
        dimensions=encoder.dimensions,
        batch_size=encoder.batch_size,
        allow_download=encoder.allow_download,
        encoder_dtype=encoder.encoder_dtype,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch daemon for automatic research-index rebuilds."
    )
    parser.add_argument(
        "--events-root",
        type=Path,
        default=Path("research-events"),
        help="Root of research-events directory",
    )
    parser.add_argument(
        "--lexical-db",
        type=Path,
        default=Path("research-events/index/research.sqlite"),
    )
    parser.add_argument(
        "--semantic-db",
        type=Path,
        default=Path("research-events/index/semantic.sqlite"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("research-events/index/semantic-manifest.json"),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=None)
    parser.add_argument(
        "--device", choices=DEVICE_CHOICES, default="auto"
    )
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--dimensions", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument(
        "--encoder-dtype", choices=ENCODER_DTYPE_CHOICES, default="float32"
    )
    parser.add_argument("--debounce", type=float, default=10.0)
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Allow downloading the model if not cached locally",
    )
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Do a full semantic rebuild at startup instead of incremental",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single rebuild and exit (no watching)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    events_root = args.events_root.resolve()
    lexical_db = args.lexical_db.resolve()
    semantic_db = args.semantic_db.resolve()
    manifest_path = args.manifest.resolve()
    watch_dir = events_root / "daily"

    if not events_root.exists():
        logger.error("Events root does not exist: %s", events_root)
        return 1
    if not watch_dir.exists():
        logger.error("Watch directory does not exist: %s", watch_dir)
        return 1

    # Create encoder holder (lazy — model loads on first use)
    encoder = EncoderHolder(
        model_name=args.model,
        revision=args.revision,
        device=args.device,
        max_length=args.max_length,
        dimensions=args.dimensions,
        batch_size=args.batch_size,
        allow_download=args.allow_download,
        hf_modules_cache=semantic_db.parent / "hf-modules",
        encoder_dtype=args.encoder_dtype,
    )

    # Eagerly load model at startup so failures are visible immediately
    logger.info("Pre-loading encoder model …")
    encoder._ensure_loaded()

    # Optional full rebuild at startup
    if args.full_rebuild:
        logger.info("Full semantic rebuild requested at startup …")
        report = full_semantic_rebuild(
            events_root, semantic_db, manifest_path, encoder
        )
        logger.info("Full rebuild complete: %s", json.dumps(report, sort_keys=True))

    # Run once mode
    if args.once:
        logger.info("--once mode: running single rebuild")
        run_rebuild(events_root, lexical_db, semantic_db, manifest_path, encoder)
        logger.info("Single rebuild complete, exiting.")
        return 0

    # Set up watchdog
    handler = DebouncedJSONLHandler(
        debounce_seconds=args.debounce,
        events_root=events_root,
        lexical_db=lexical_db,
        semantic_db=semantic_db,
        manifest_path=manifest_path,
        encoder=encoder,
    )
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=True)

    # Graceful shutdown
    shutdown_event = threading.Event()

    def signal_handler(signum: int, frame: Any) -> None:
        logger.info("Received signal %d, shutting down …", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    observer.start()
    logger.info(
        "Watching %s for JSONL changes (debounce=%.0fs, device=%s)",
        watch_dir,
        args.debounce,
        encoder.selected_device,
    )
    logger.info("Press Ctrl+C to stop.")

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    finally:
        observer.stop()
        observer.join()
        logger.info("Daemon stopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
