# ruff: noqa: E402

import pytest


pytest.skip(
    "legacy code-search builder was not migrated into the universal package; "
    "preserve these fixtures until a bounded adapter is restored",
    allow_module_level=True,
)

import sqlite3
from pathlib import Path

import numpy as np

from scripts.build_code_search_index import build
from scripts.query_research_code import query_code


def test_exact_identifier_and_bm25_rank_before_unrelated_symbols(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "loss.py").write_text(
        "def compute_pair_loss(start_logits, end_logits):\n    return start_logits + end_logits\n\n"
        "def generic_helper(value):\n    return value\n",
        encoding="utf-8",
    )
    database = tmp_path / "code.sqlite"
    build(tmp_path, database, tmp_path / "manifest.jsonl")
    with sqlite3.connect(database) as connection:
        results = query_code(connection, "compute_pair_loss", 5)
    assert results[0]["qualified_name"] == "compute_pair_loss"
    assert results[0]["retrieval"]["exact_boost"] >= 100
    assert results[0]["path"] == "scripts/loss.py"
    assert results[0]["start_line"] == 1
    assert results[0]["source_commit"]
    assert results[0]["retrieval"]["query_mode"] == "identifier"


def test_korean_description_prefers_dense_function_over_incidental_lexical_match(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "loss.py").write_text(
        "def joint_loss(output, batch):\n    return output + batch\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_loss.py").write_text(
        'GLOSSARY = {"span": "끝점과 시작점을 묶는 손실"}\n',
        encoding="utf-8",
    )
    database = tmp_path / "code.sqlite"
    build(tmp_path, database, tmp_path / "manifest.jsonl")
    semantic = tmp_path / "semantic.sqlite"
    with sqlite3.connect(database) as code, sqlite3.connect(semantic) as dense:
        symbols = dict(code.execute("SELECT qualified_name, symbol_id FROM symbols"))
        dense.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        dense.execute(
            "CREATE TABLE embeddings (symbol_id TEXT PRIMARY KEY, vector BLOB NOT NULL)"
        )
        dense.execute("INSERT INTO metadata VALUES ('dimensions', '2')")
        dense.executemany(
            "INSERT INTO embeddings VALUES (?, ?)",
            [
                (
                    symbols["joint_loss"],
                    np.asarray([1.0, 0.0], dtype=np.float32).tobytes(),
                ),
                (
                    symbols["GLOSSARY"],
                    np.asarray([0.0, 1.0], dtype=np.float32).tobytes(),
                ),
            ],
        )
        results = query_code(
            code,
            "끝점과 시작점을 묶는 손실",
            2,
            dense,
            np.asarray([1.0, 0.0], dtype=np.float32),
        )
    assert results[0]["qualified_name"] == "joint_loss"
    assert results[0]["retrieval"]["query_mode"] == "natural_language"
    assert (
        results[0]["retrieval"]["dense_weight"]
        > results[0]["retrieval"]["lexical_weight"]
    )
