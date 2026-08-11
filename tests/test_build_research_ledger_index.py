import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from universal_research_mcp.tools.build_research_ledger_index import build
from universal_research_mcp.tools.query_research_ledger import query


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def event(event_id: str, date: str, summary: str, status: str = "completed") -> dict:
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "date": date,
        "event_type": "gate_result",
        "status": status,
        "project": "test",
        "workstream": "test",
        "summary": summary,
        "relations": [{"type": "derived_from", "target": "evt_seed"}],
        "artifacts": [{"path": "docs/source.md", "sha256": "abc", "role": "result"}],
        "source": {
            "source_path": "docs/source.md",
            "source_sha256": "abc",
            "heading": "2026-07-19 Result",
            "line_start": 1,
            "line_end": 4,
            "legacy_import": True,
            "requires_human_review": True,
        },
    }


def test_build_and_query_sqlite_fts_relation_index(tmp_path: Path) -> None:
    root = tmp_path / "research-events"
    write_jsonl(
        root / "sources.jsonl",
        [{"source_id": "src_1", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": True}],
    )
    write_jsonl(root / "daily" / "2026-07-19" / "events.jsonl", [event("evt_qwen", "2026-07-19", "Qwen sealed evaluation inconclusive")])
    database = root / "index" / "research.sqlite"
    report = build(root, database)

    assert report["event_count"] == "1"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        results = query(connection, Namespace(query="Qwen sealed?", date=None, status=None, event_type=None, source_path=None, related_to=None, limit=5))
    assert len(results) == 1
    assert results[0]["event_id"] == "evt_qwen"
    assert results[0]["relations"] == [{"type": "derived_from", "target": "evt_seed"}]


def test_build_accepts_string_artifact_paths(tmp_path: Path) -> None:
    root = tmp_path / "research-events"
    write_jsonl(
        root / "sources.jsonl",
        [{"source_id": "src_1", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}],
    )
    row = event("evt_string_artifact", "2026-07-27", "String artifact compatibility")
    row["artifacts"] = ["results/new.json", {"path": "results/old.json", "role": "result"}]
    write_jsonl(root / "daily" / "2026-07-27" / "events.jsonl", [row])

    database = root / "index" / "research.sqlite"
    build(root, database)

    with sqlite3.connect(database) as connection:
        artifacts = connection.execute("SELECT path, sha256, role FROM artifacts ORDER BY path").fetchall()
    assert artifacts == [("results/new.json", None, None), ("results/old.json", None, "result")]


def test_build_accepts_legacy_string_relations_without_mutating_jsonl(tmp_path: Path) -> None:
    root = tmp_path / "research-events"
    write_jsonl(
        root / "sources.jsonl",
        [{"source_id": "src_1", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}],
    )
    row = event("evt_string_relation", "2026-07-28", "Legacy relation compatibility")
    row["relations"] = [
        "evt_legacy_parent",
        {"type": "validates", "target": "evt_explicit_parent"},
    ]
    events_path = root / "daily" / "2026-07-28" / "events.jsonl"
    write_jsonl(events_path, [row])
    canonical_before = events_path.read_bytes()

    database = root / "index" / "research.sqlite"
    build(root, database)

    assert events_path.read_bytes() == canonical_before
    with sqlite3.connect(database) as connection:
        relations = connection.execute(
            "SELECT relation_type, target FROM relations ORDER BY relation_type, target"
        ).fetchall()
        results = query(
            connection,
            Namespace(
                query=None,
                date=None,
                status=None,
                event_type=None,
                source_path=None,
                related_to="evt_legacy_parent",
                limit=5,
            ),
        )

    assert relations == [
        ("derived_from", "evt_legacy_parent"),
        ("validates", "evt_explicit_parent"),
    ]
    assert [result["event_id"] for result in results] == ["evt_string_relation"]


def test_build_applies_append_only_source_range_correction_to_derived_columns(
    tmp_path: Path,
) -> None:
    root = tmp_path / "research-events"
    write_jsonl(
        root / "sources.jsonl",
        [{"source_id": "src_1", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}],
    )
    original = event("evt_original", "2026-08-03", "Original source range")
    original["source"]["line_end"] = 5
    correction = event("evt_correction", "2026-08-03", "Correct source range")
    correction.update(
        {
            "event_type": "amendment",
            "relations": [{"type": "corrects", "target": "evt_original"}],
            "observed": {
                "corrected_event_id": "evt_original",
                "corrected_json_pointer": "/source/line_end",
                "recorded_line_end": 5,
                "corrected_line_end": 4,
            },
        }
    )
    events_path = root / "daily" / "2026-08-03" / "events.jsonl"
    write_jsonl(events_path, [original, correction])
    canonical_before = events_path.read_bytes()

    database = root / "index" / "research.sqlite"
    build(root, database)

    assert events_path.read_bytes() == canonical_before
    with sqlite3.connect(database) as connection:
        line_end, raw_json = connection.execute(
            "SELECT line_end, raw_json FROM events WHERE event_id='evt_original'"
        ).fetchone()
        correction_count = connection.execute(
            "SELECT value FROM metadata WHERE key='source_range_correction_count'"
        ).fetchone()[0]
    assert line_end == 4
    assert json.loads(raw_json)["source"]["line_end"] == 5
    assert correction_count == "1"


def test_build_rejects_malformed_canonical_jsonl(tmp_path: Path) -> None:
    root = tmp_path / "research-events"
    write_jsonl(
        root / "sources.jsonl",
        [{"source_id": "src_1", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}],
    )
    path = root / "daily" / "2026-08-04" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"event_id":\n', encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        build(root, root / "index" / "research.sqlite")


def test_build_handles_large_fixture_ledger_without_altering_canonical_input(tmp_path: Path) -> None:
    root = tmp_path / "research-events"
    write_jsonl(
        root / "sources.jsonl",
        [{"source_id": "src_1", "source_path": "docs/source.md", "source_sha256": "abc", "source_type": "markdown", "legacy_import": False}],
    )
    rows = [event(f"evt_{number:04d}", "2026-08-04", f"Fixture evidence {number}") for number in range(1000)]
    path = root / "daily" / "2026-08-04" / "events.jsonl"
    write_jsonl(path, rows)
    canonical_before = path.read_bytes()

    report = build(root, root / "index" / "research.sqlite")

    assert report["event_count"] == "1000"
    assert path.read_bytes() == canonical_before
