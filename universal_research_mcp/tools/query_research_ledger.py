#!/usr/bin/env python3
"""Query the derived research ledger and return source-grounded retrieval results."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universal_research_mcp.tools.research_device import DEVICE_CHOICES, resolve_torch_device
from universal_research_mcp.tools.build_research_semantic_index import (
    ENCODER_COMPATIBILITY_BRIDGE_VERSION,
    apply_encoder_compatibility_bridge,
    normalize_vectors,
    resolve_encoder_dtype,
)


def fts_terms(query_text: str) -> str | None:
    """Turn a natural-language question into safe broad FTS5 candidate terms."""
    terms = re.findall(r"[0-9A-Za-z가-힣_]+", query_text)
    return " OR ".join(terms) if terms else None


def query(connection: sqlite3.Connection, args: argparse.Namespace) -> list[dict]:
    filters: list[str] = []
    params: list[object] = []
    joins = ""
    rank = "0.0"
    lexical_query = fts_terms(args.query) if args.query else None
    if lexical_query:
        joins = " JOIN event_fts ON event_fts.event_id = e.event_id "
        filters.append("event_fts MATCH ?")
        params.append(lexical_query)
        rank = "bm25(event_fts)"
    if args.date:
        filters.append("e.date = ?")
        params.append(args.date)
    if args.status:
        filters.append("e.status = ?")
        params.append(args.status)
    if args.event_type:
        filters.append("e.event_type = ?")
        params.append(args.event_type)
    if args.source_path:
        filters.append("e.source_path = ?")
        params.append(args.source_path)
    if args.related_to:
        joins += " JOIN relations r ON r.event_id = e.event_id "
        filters.append("r.target = ?")
        params.append(args.related_to)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    statement = f"""
        SELECT e.event_id, e.date, e.event_type, e.status, e.summary,
               e.source_path, e.source_heading, e.line_start, e.line_end,
               e.legacy_import, e.requires_human_review, {rank} AS rank
        FROM events e {joins} {where}
        ORDER BY rank, e.date DESC, e.event_id ASC
        LIMIT ?
    """
    params.append(args.limit)
    rows = connection.execute(statement, params).fetchall()
    columns = [column[0] for column in connection.execute(statement, params).description]
    results = [dict(zip(columns, row)) for row in rows]
    for result in results:
        result["relations"] = [
            {"type": row[0], "target": row[1]}
            for row in connection.execute(
                "SELECT relation_type, target FROM relations WHERE event_id = ? ORDER BY relation_type, target",
                (result["event_id"],),
            )
        ]
        result["artifacts"] = [
            {"path": row[0], "sha256": row[1], "role": row[2]}
            for row in connection.execute(
                "SELECT path, sha256, role FROM artifacts WHERE event_id = ? ORDER BY path",
                (result["event_id"],),
            )
        ]
    return results


def semantic_rank_details(connection: sqlite3.Connection, vector: np.ndarray, limit: int) -> list[dict]:
    """Rank events and retain the exact source passage when it wins.

    Dense retrieval is only a candidate generator.  Returning the winning
    passage's path and line interval keeps the answer auditable instead of
    presenting a similarity score as research evidence.
    """
    dimensions = connection.execute("SELECT value FROM metadata WHERE key = 'dimensions'").fetchone()
    if not dimensions:
        raise ValueError("Semantic index does not declare dimensions")
    if int(dimensions[0]) != vector.size:
        raise ValueError("Query vector dimension does not match semantic index")
    candidates: list[dict] = []
    for event_id, blob in connection.execute("SELECT event_id, vector FROM embeddings"):
        candidate = np.frombuffer(blob, dtype=np.float32)
        candidates.append({"event_id": event_id, "cosine_similarity": float(np.dot(vector, candidate))})
    try:
        for passage_id, event_id, source_path, source_heading, line_start, line_end, blob in connection.execute(
            "SELECT passage_id, event_id, source_path, source_heading, line_start, line_end, vector "
            "FROM passage_embeddings"
        ):
            candidate = np.frombuffer(blob, dtype=np.float32)
            candidates.append(
                {
                    "event_id": event_id,
                    "cosine_similarity": float(np.dot(vector, candidate)),
                    "semantic_evidence": {
                        "kind": "source_passage",
                        "passage_id": passage_id,
                        "source_path": source_path,
                        "source_heading": source_heading,
                        "line_start": line_start,
                        "line_end": line_end,
                    },
                }
            )
    except sqlite3.OperationalError:
        pass
    best_by_event: dict[str, dict] = {}
    for item in candidates:
        existing = best_by_event.get(item["event_id"])
        if existing is None or item["cosine_similarity"] > existing["cosine_similarity"]:
            best_by_event[item["event_id"]] = item
    return sorted(best_by_event.values(), key=lambda item: (-item["cosine_similarity"], item["event_id"]))[:limit]


def semantic_rank(connection: sqlite3.Connection, vector: np.ndarray, limit: int) -> list[tuple[str, float]]:
    """Compatibility wrapper used by the regression evaluator."""
    return [
        (item["event_id"], item["cosine_similarity"])
        for item in semantic_rank_details(connection, vector, limit)
    ]


def remote_code_kwargs(snapshot: Path) -> dict[str, str]:
    """Return kwargs that bind a cached remote-code revision during offline load."""
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
    from huggingface_hub import snapshot_download

    dependency_snapshot = Path(snapshot_download(repositories[0], local_files_only=True))
    return {"code_revision": dependency_snapshot.name}


def load_semantic_encoder(semantic_connection: sqlite3.Connection, device: str):
    metadata = dict(semantic_connection.execute("SELECT key, value FROM metadata"))
    snapshot = metadata.get("snapshot_path")
    snapshot_path = Path(snapshot) if snapshot else None
    if snapshot_path is None or not snapshot_path.exists():
        model_name = metadata.get("model")
        model_revision = metadata.get("model_revision")
        if not model_name or not model_revision:
            raise FileNotFoundError("Pinned semantic model snapshot is absent and metadata lacks model/revision")
        from huggingface_hub import snapshot_download

        try:
            snapshot_path = Path(
                snapshot_download(model_name, revision=model_revision, local_files_only=True)
            )
        except Exception as exc:
            raise FileNotFoundError(
                "Pinned semantic model snapshot is absent; rebuild with the recorded model revision"
            ) from exc
    recorded_modules_cache = Path(metadata.get("hf_modules_cache", ""))
    db_modules_cache = Path(semantic_connection.execute("PRAGMA database_list").fetchone()[2]).parent / "hf-modules"
    modules_cache = recorded_modules_cache if recorded_modules_cache.exists() else db_modules_cache
    modules_cache.mkdir(parents=True, exist_ok=True)
    os.environ["HF_MODULES_CACHE"] = str(modules_cache)
    from transformers import dynamic_module_utils

    dynamic_module_utils.HF_MODULES_CACHE = str(modules_cache)
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - CLI environment behavior
        raise RuntimeError("sentence-transformers is required for semantic query") from exc
    selected_device = resolve_torch_device(device)
    encoder_kwargs = remote_code_kwargs(snapshot_path)
    encoder_dtype = metadata.get("encoder_dtype")
    model_kwargs: dict[str, object] = dict(encoder_kwargs)
    if encoder_dtype is not None:
        model_kwargs["torch_dtype"] = resolve_encoder_dtype(encoder_dtype)
    model = SentenceTransformer(
        str(snapshot_path), trust_remote_code=True, local_files_only=True, device=selected_device,
        config_kwargs=encoder_kwargs, model_kwargs=model_kwargs,
    )

    if encoder_dtype is not None:
        loaded_dtype = next(model.parameters()).dtype
        requested_dtype = resolve_encoder_dtype(encoder_dtype)
        if loaded_dtype != requested_dtype:
            raise ValueError(
                f"Query encoder dtype mismatch: requested {requested_dtype}, loaded {loaded_dtype}"
            )

    model.max_seq_length = int(metadata.get("max_length", "512"))
    bridge_report = apply_encoder_compatibility_bridge(model, snapshot_path)
    recorded_bridge = metadata.get("encoder_compatibility_bridge")
    if recorded_bridge is not None and recorded_bridge != ENCODER_COMPATIBILITY_BRIDGE_VERSION:
        raise ValueError(
            "Semantic index compatibility bridge mismatch: "
            f"recorded {recorded_bridge}, runtime {ENCODER_COMPATIBILITY_BRIDGE_VERSION}"
        )
    recorded_tensor_count = metadata.get("encoder_restored_tensor_count")
    if recorded_tensor_count is not None and int(recorded_tensor_count) != int(
        bridge_report["restored_tensor_count"]
    ):
        raise ValueError("Semantic index restored-tensor count differs from runtime")
    dimensions = int(metadata["dimensions"])
    return model, dimensions


def encode_semantic_texts(model, dimensions: int, texts: list[str], batch_size: int = 8) -> np.ndarray:
    vectors = model.encode(
        texts, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=False, show_progress_bar=False
    ).astype(np.float32)
    return normalize_vectors(vectors, dimensions)


def encode_semantic_query(query_text: str, semantic_connection: sqlite3.Connection, device: str) -> np.ndarray:
    model, dimensions = load_semantic_encoder(semantic_connection, device)
    return encode_semantic_texts(model, dimensions, [query_text])[0]


def matches_filters(result: dict, args: argparse.Namespace) -> bool:
    if args.date and result["date"] != args.date:
        return False
    if args.status and result["status"] != args.status:
        return False
    if args.event_type and result["event_type"] != args.event_type:
        return False
    if args.source_path and result["source_path"] != args.source_path:
        return False
    if args.related_to and args.related_to not in {item["target"] for item in result["relations"]}:
        return False
    return True


def hybrid_query(
    ledger_connection: sqlite3.Connection,
    semantic_connection: sqlite3.Connection,
    args: argparse.Namespace,
    query_vector: np.ndarray,
) -> list[dict]:
    lexical = query(ledger_connection, args) if args.query else []
    all_args = argparse.Namespace(
        query=None, date=None, status=None, event_type=None, source_path=None, related_to=None, limit=100000
    )
    all_results = {item["event_id"]: item for item in query(ledger_connection, all_args)}
    semantic = semantic_rank_details(semantic_connection, query_vector, max(args.limit * 8, 50))
    rrf: dict[str, dict[str, float]] = {}
    for rank, item in enumerate(lexical, start=1):
        rrf.setdefault(item["event_id"], {})["bm25_rank"] = float(rank)
    for rank, item in enumerate(semantic, start=1):
        event_id = item["event_id"]
        rrf.setdefault(event_id, {})["semantic_rank"] = float(rank)
        rrf[event_id]["cosine_similarity"] = item["cosine_similarity"]
        if "semantic_evidence" in item:
            rrf[event_id]["semantic_evidence"] = item["semantic_evidence"]
    fused = []
    for event_id, values in rrf.items():
        result = all_results.get(event_id)
        if not result or not matches_filters(result, args):
            continue
        score = sum(1.0 / (60 + value) for key, value in values.items() if key.endswith("_rank"))
        result = {**result, "retrieval": {**values, "rrf_score": score}}
        fused.append(result)
    return sorted(fused, key=lambda item: (-item["retrieval"]["rrf_score"], item["event_id"]))[: args.limit]


def semantic_query(
    ledger_connection: sqlite3.Connection,
    semantic_connection: sqlite3.Connection,
    args: argparse.Namespace,
    query_vector: np.ndarray,
) -> list[dict]:
    """Return dense candidates directly, retaining the matching passage provenance."""
    all_args = argparse.Namespace(
        query=None, date=None, status=None, event_type=None, source_path=None, related_to=None, limit=100000
    )
    all_results = {item["event_id"]: item for item in query(ledger_connection, all_args)}
    results = []
    for rank, item in enumerate(semantic_rank_details(semantic_connection, query_vector, 100000), start=1):
        result = all_results.get(item["event_id"])
        if not result or not matches_filters(result, args):
            continue
        retrieval = {
            "semantic_rank": float(rank),
            "cosine_similarity": item["cosine_similarity"],
        }
        if "semantic_evidence" in item:
            retrieval["semantic_evidence"] = item["semantic_evidence"]
        results.append({**result, "retrieval": retrieval})
        if len(results) == args.limit:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--semantic-db", type=Path)
    parser.add_argument("--semantic-device", choices=DEVICE_CHOICES, default="auto")
    parser.add_argument(
        "--retrieval-mode", choices=("semantic", "hybrid"), default="semantic",
        help="Dense-only is the default; hybrid RRF remains an explicit experimental comparison mode.",
    )
    parser.add_argument("--query")
    parser.add_argument("--date")
    parser.add_argument("--status")
    parser.add_argument("--event-type")
    parser.add_argument("--source-path")
    parser.add_argument("--related-to")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    with sqlite3.connect(args.db) as connection:
        if args.semantic_db:
            if not args.query:
                parser.error("--semantic-db requires --query")
            with sqlite3.connect(args.semantic_db) as semantic_connection:
                query_vector = encode_semantic_query(args.query, semantic_connection, args.semantic_device)
                if args.retrieval_mode == "semantic":
                    results = semantic_query(connection, semantic_connection, args, query_vector)
                else:
                    results = hybrid_query(connection, semantic_connection, args, query_vector)
        else:
            results = query(connection, args)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    for result in results:
        print(f"{result['event_id']} | {result['date']} | {result['event_type']} | {result['status']}")
        print(f"  {result['summary']}")
        source = result.get("source_path") or "not recorded"
        heading = result.get("source_heading") or ""
        lines = f"{result.get('line_start') or 'n/a'}–{result.get('line_end') or 'n/a'}"
        print(f"  source: {source} | {heading} | lines {lines}")
        if "retrieval" in result:
            print(f"  retrieval: {json.dumps(result['retrieval'], ensure_ascii=False, sort_keys=True)}")
            if evidence := result["retrieval"].get("semantic_evidence"):
                print(
                    "  semantic evidence: "
                    f"{evidence['source_path']} | {evidence['source_heading']} | "
                    f"lines {evidence['line_start']}–{evidence['line_end']}"
                )
        if result["requires_human_review"]:
            print("  provenance: legacy import; human review required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
