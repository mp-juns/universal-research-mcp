# ruff: noqa: E402

import pytest


pytest.skip(
    "legacy project-specific search daemon is intentionally outside the "
    "universal package; keep these fixtures for a future compatibility adapter",
    allow_module_level=True,
)

from tools.project_search.mcp_server import (
    _dedupe_candidates,
    _event_recency_timestamp,
    _event_recency_sort_key,
    list_latest_research_events,
)
from tools.project_search.server import SearchRequest, fuse_results


def test_dedupe_candidates_keeps_best_ranked_passage_per_event() -> None:
    candidates = [
        {"event_id": "evt_one", "passage_id": "psg_best", "semantic_score": 0.9},
        {"event_id": "evt_one", "passage_id": "psg_other", "semantic_score": 0.8},
        {"event_id": "evt_two", "passage_id": "psg_two", "semantic_score": 0.7},
    ]

    results = _dedupe_candidates(candidates)

    assert [result["event_id"] for result in results] == ["evt_one", "evt_two"]
    assert results[0]["passage_id"] == "psg_best"


def test_dedupe_candidates_falls_back_to_passage_identity() -> None:
    candidates = [
        {"path": "a.md", "start_line": 1, "end_line": 2, "passage_id": "psg_a"},
        {"path": "a.md", "start_line": 1, "end_line": 2, "passage_id": "psg_a"},
        {"path": "a.md", "start_line": 3, "end_line": 4, "passage_id": "psg_b"},
    ]

    assert _dedupe_candidates(candidates) == [candidates[0], candidates[2]]


def test_hybrid_fusion_returns_diverse_events_and_keeps_best_passage() -> None:
    request = SearchRequest(
        query="hybrid decision",
        top_k=3,
        mode="hybrid",
        lexical_weight=0.25,
        semantic_weight=0.75,
    )
    lexical = [
        {
            "kind": "event",
            "event_id": "evt_one",
            "lexical_score": 1.0,
            "semantic_score": 0.0,
        }
    ]
    semantic = [
        {
            "kind": "passage",
            "event_id": "evt_one",
            "passage_id": "psg_best",
            "lexical_score": 0.0,
            "semantic_score": 0.9,
        },
        {
            "kind": "passage",
            "event_id": "evt_one",
            "passage_id": "psg_other",
            "lexical_score": 0.0,
            "semantic_score": 0.8,
        },
        {
            "kind": "passage",
            "event_id": "evt_two",
            "passage_id": "psg_two",
            "lexical_score": 0.0,
            "semantic_score": 0.7,
        },
    ]

    results = fuse_results(lexical, semantic, request)

    assert [result["event_id"] for result in results] == ["evt_one", "evt_two"]
    assert results[0]["passage_id"] == "psg_best"


def test_event_recency_timestamp_prefers_timestamp_end() -> None:
    raw = (
        '{"timestamp_start":"2026-07-27T03:00:00+09:00",'
        '"timestamp_end":"2026-07-27T22:29:41+09:00"}'
    )
    assert _event_recency_timestamp(raw, "2026-07-27") == "2026-07-27T22:29:41+09:00"
    assert (
        _event_recency_timestamp(
            '{"timestamp_start":"2026-07-27T03:00:00+09:00"}',
            "2026-07-27",
        )
        == "2026-07-27T03:00:00+09:00"
    )
    assert _event_recency_timestamp("{}", "2026-07-27") == "2026-07-27"
    assert _event_recency_timestamp("not-json", "2026-07-26") == "2026-07-26"


def test_event_recency_sort_key_normalizes_mixed_offsets() -> None:
    utc = _event_recency_sort_key(
        '{"timestamp_end":"2026-08-02T16:03:04Z"}', "2026-08-03"
    )
    kst_older = _event_recency_sort_key(
        '{"timestamp_end":"2026-08-03T01:02:00+09:00"}', "2026-08-03"
    )
    kst_newer = _event_recency_sort_key(
        '{"timestamp_end":"2026-08-03T01:04:00+09:00"}', "2026-08-03"
    )

    assert kst_older < utc < kst_newer


def test_list_latest_research_events_orders_by_timestamp_not_event_id(
    tmp_path,
) -> None:
    import sqlite3

    db_path = tmp_path / "research.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        db.executemany(
            """
            INSERT INTO events(event_id, date, event_type, status, summary, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "evt_20260727_unoq_mid_bench_v128k_6l_vs_8l",
                    "2026-07-27",
                    "run_finished",
                    "completed",
                    "older mid-bench",
                    '{"timestamp_end":"2026-07-27T03:18:00+09:00"}',
                ),
                (
                    "evt_20260727_qwen35_suffix_continuation_unoq_v1",
                    "2026-07-27",
                    "run_finished",
                    "completed",
                    "newer suffix unoq",
                    '{"timestamp_end":"2026-07-27T22:29:41+09:00"}',
                ),
                (
                    "evt_20260727_qwen35_suffix_continuation_local_v1",
                    "2026-07-27",
                    "run_finished",
                    "completed",
                    "local suffix",
                    '{"timestamp_end":"2026-07-27T21:41:36+09:00"}',
                ),
                (
                    "evt_20260727_cross_offset_newer",
                    "2026-07-27",
                    "run_finished",
                    "completed",
                    "newer absolute UTC instant",
                    '{"timestamp_end":"2026-07-27T13:31:00Z"}',
                ),
                (
                    "ref_new_reference",
                    "2099-01-01",
                    "reference_document",
                    "available",
                    "reference records must not enter the latest-event feed",
                    "{}",
                ),
            ],
        )
        db.commit()
        db.row_factory = sqlite3.Row
        results = list_latest_research_events(db, top_k=4)

    assert [row["event_id"] for row in results] == [
        "evt_20260727_cross_offset_newer",
        "evt_20260727_qwen35_suffix_continuation_unoq_v1",
        "evt_20260727_qwen35_suffix_continuation_local_v1",
        "evt_20260727_unoq_mid_bench_v128k_6l_vs_8l",
    ]
    # Lexicographic event_id DESC would put unoq_mid_bench first; timestamp must win.
    assert results[0]["summary"] == "newer absolute UTC instant"
