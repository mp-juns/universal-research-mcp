from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from scripts import watch_research_index as watcher
from scripts.build_research_semantic_index import initialize


class _FakeEncoder:
    dimensions = 4
    model_name = "test/model"
    resolved_revision = "test-revision"
    selected_device = "cpu"
    max_length = 32
    encoder_dtype = "float32"
    _bridge_report = {"restored_tensor_count": 2, "repaired_buffer_count": 4}

    def __init__(self, snapshot: Path) -> None:
        self.snapshot = snapshot

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), self.dimensions), dtype=np.float32)


def test_incremental_metadata_keeps_references_separate(
    tmp_path: Path, monkeypatch
) -> None:
    semantic_db = tmp_path / "semantic.sqlite"
    manifest_path = tmp_path / "semantic-manifest.json"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    with sqlite3.connect(semantic_db) as connection:
        initialize(connection)
        vector = np.ones(4, dtype=np.float32).tobytes()
        connection.executemany(
            "INSERT INTO embeddings VALUES (?, ?, ?, ?)",
            [
                ("evt_existing", 4, vector, "event-sha"),
                ("ref_existing", 4, vector, "reference-sha"),
            ],
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [("event_count", "1"), ("reference_count", "1")],
        )
        connection.commit()

    events = [
        {
            "event_id": "evt_existing",
            "event_type": "observation",
            "status": "completed",
            "summary": "existing",
        },
        {
            "event_id": "ref_existing",
            "event_type": "reference_document",
            "status": "completed",
            "summary": "reference",
            "reference_extraction": {
                "pdf": True,
                "page_count": 7,
                "sparse_pages": [3],
                "ocr_pages": [3],
            },
        },
        {
            "event_id": "evt_new",
            "event_type": "run_finished",
            "status": "completed",
            "summary": "new",
        },
    ]
    monkeypatch.setattr(
        watcher, "events_and_fingerprint", lambda _root: (events, "bundle-sha")
    )
    monkeypatch.setattr(watcher, "source_passages", lambda _event, _root: [])
    monkeypatch.setattr(watcher, "snapshot_hashes", lambda _snapshot: {})
    monkeypatch.setattr(watcher, "remote_code_dependencies", lambda _snapshot: [])

    report = watcher.incremental_semantic_rebuild(
        tmp_path / "research-events",
        semantic_db,
        manifest_path,
        _FakeEncoder(snapshot),
    )

    with sqlite3.connect(semantic_db) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert report["new_events"] == 1
    assert report["total_events"] == 2
    assert report["total_embeddings"] == 3
    assert metadata["event_count"] == "2"
    assert metadata["reference_count"] == "1"
    assert metadata["embedding_count"] == "3"
    assert metadata["reference_pdf_count"] == "1"
    assert metadata["reference_pdf_page_count"] == "7"
    assert metadata["reference_sparse_pdf_page_count"] == "1"
    assert metadata["reference_ocr_pdf_page_count"] == "1"
    assert metadata["encoder_dtype"] == "float32"
    assert metadata["encoder_smoke_count"] == "1"
    assert metadata["encoder_compatibility_bridge"] == watcher.ENCODER_COMPATIBILITY_BRIDGE_VERSION
    assert metadata["encoder_restored_tensor_count"] == "2"
    assert metadata["encoder_repaired_buffer_count"] == "4"
    assert metadata["encoder_oracle_status"] == "passed"
    assert manifest["event_count"] == "2"
    assert manifest["reference_count"] == "1"
    assert manifest["embedding_count"] == "3"
    assert manifest["encoder_dtype"] == "float32"
    assert manifest["encoder_smoke_count"] == "1"
    assert manifest["encoder_compatibility_bridge"] == watcher.ENCODER_COMPATIBILITY_BRIDGE_VERSION


def test_incremental_correction_replaces_existing_target_passages(
    tmp_path: Path, monkeypatch
) -> None:
    events_root = tmp_path / "research-events"
    daily = events_root / "daily" / "2026-08-03" / "events.jsonl"
    daily.parent.mkdir(parents=True)
    (events_root / "sources.jsonl").write_text("", encoding="utf-8")

    source_path = tmp_path / "evidence.md"
    source_path.write_text("# Evidence\nalpha\n\nbeta\ngamma\n", encoding="utf-8")
    target = {
        "event_id": "evt_target",
        "event_type": "observation",
        "status": "completed",
        "summary": "target before correction",
        "source": {
            "source_path": "evidence.md",
            "heading": "Evidence",
            "source_sha256": "synthetic",
            "line_start": 1,
            "line_end": 5,
        },
    }
    daily.write_text(json.dumps(target) + "\n", encoding="utf-8")

    semantic_db = events_root / "index" / "semantic.sqlite"
    semantic_db.parent.mkdir(parents=True)
    manifest_path = semantic_db.with_name("semantic-manifest.json")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    with sqlite3.connect(semantic_db) as connection:
        initialize(connection)

    monkeypatch.setattr(watcher, "snapshot_hashes", lambda _snapshot: {})
    monkeypatch.setattr(watcher, "remote_code_dependencies", lambda _snapshot: [])
    encoder = _FakeEncoder(snapshot)

    first = watcher.incremental_semantic_rebuild(
        events_root, semantic_db, manifest_path, encoder
    )
    with sqlite3.connect(semantic_db) as connection:
        old_rows = connection.execute(
            "SELECT passage_id, line_start, line_end, retrieval_text_sha256 "
            "FROM passage_embeddings WHERE event_id = ? ORDER BY passage_id",
            (target["event_id"],),
        ).fetchall()
    assert first["new_events"] == 1
    assert old_rows

    correction = {
        "event_id": "evt_correction",
        "event_type": "amendment",
        "status": "completed",
        "summary": "correct target range",
        "observed": {
            "corrected_event_id": target["event_id"],
            "corrected_json_pointer": "/source/line_end",
            "recorded_line_end": 5,
            "corrected_line_end": 2,
        },
        "relations": [{"target": target["event_id"], "type": "corrects"}],
    }
    daily.write_text(
        "\n".join(json.dumps(event) for event in (target, correction)) + "\n",
        encoding="utf-8",
    )

    second = watcher.incremental_semantic_rebuild(
        events_root, semantic_db, manifest_path, encoder
    )
    corrected_target = {
        **target,
        "source": {**target["source"], "line_end": 2},
    }
    expected_rows = [
        (
            passage["passage_id"],
            passage["line_start"],
            passage["line_end"],
            passage["text_sha256"],
        )
        for passage in watcher.source_passages(corrected_target, tmp_path)
    ]
    with sqlite3.connect(semantic_db) as connection:
        actual_rows = connection.execute(
            "SELECT passage_id, line_start, line_end, retrieval_text_sha256 "
            "FROM passage_embeddings WHERE event_id = ? ORDER BY passage_id",
            (target["event_id"],),
        ).fetchall()

    assert second["new_events"] == 1
    assert second["new_passages"] == 0
    assert second["refreshed_events"] == 1
    assert second["refreshed_passages"] == len(expected_rows)
    assert actual_rows == expected_rows
    assert not set(actual_rows) & set(old_rows)
