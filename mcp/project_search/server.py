from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, Literal

import numpy as np

from .query_expansion import build_query_variants
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(
    os.environ.get(
        "PROJECT_SEARCH_ROOT",
        Path(__file__).resolve().parents[2],
    )
).resolve()

RESEARCH_DB = Path(
    os.environ.get(
        "PROJECT_SEARCH_RESEARCH_DB",
        ROOT / "data/index/research.sqlite",
    )
).resolve()

SEMANTIC_DB = Path(
    os.environ.get(
        "PROJECT_SEARCH_SEMANTIC_DB",
        ROOT / "data/index/semantic.sqlite",
    )
).resolve()

SEMANTIC_MANIFEST = Path(
    os.environ.get(
        "PROJECT_SEARCH_SEMANTIC_MANIFEST",
        ROOT / "data/index/semantic-manifest.json",
    )
).resolve()

API_KEY = os.environ.get("PROJECT_SEARCH_API_KEY", "")
DEVICE = os.environ.get("PROJECT_SEARCH_DEVICE", "cuda")
TOP_K_MAX = int(os.environ.get("PROJECT_SEARCH_TOP_K_MAX", "100"))
MAX_FETCH_LINES = int(os.environ.get("PROJECT_SEARCH_MAX_FETCH_LINES", "500"))
MODEL_IDLE_SECONDS = int(os.environ.get("PROJECT_SEARCH_MODEL_IDLE_SECONDS", "300"))

DENIED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "authorized_keys",
    "credentials.json",
}

DENIED_FRAGMENTS = {
    "secret",
    "token",
    "credential",
    "private_key",
    "api_key",
    "apikey",
}

app = FastAPI(
    title="Project Search API",
    version="0.1.0",
    description="Read-only BM25 and semantic search over research memory.",
)

_model = None
_model_lock = Lock()
_model_last_used = 0.0

_passage_matrix: np.ndarray | None = None
_passage_rows: list[dict[str, Any]] = []
_passage_lock = Lock()


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=20, ge=1, le=TOP_K_MAX)
    mode: Literal["hybrid", "lexical", "semantic"] = "hybrid"
    lexical_weight: float = Field(default=0.45, ge=0.0, le=1.0)
    semantic_weight: float = Field(default=0.55, ge=0.0, le=1.0)
    include_raw_json: bool = False
    project: str | None = None
    workstream: str | None = None
    status: str | None = None


class FetchRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=200, ge=1)
    context_lines: int = Field(default=0, ge=0, le=50)


def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="PROJECT_SEARCH_API_KEY is not configured",
        )

    supplied = x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()

    if not supplied or not hmac.compare_digest(supplied, API_KEY):
        raise HTTPException(status_code=401, detail="invalid API key")


def open_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError(f"database not found: {path}")

    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
        check_same_thread=False,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    with SEMANTIC_MANIFEST.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_model():
    global _model, _model_last_used

    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            manifest = load_manifest()
            snapshot = Path(str(manifest["snapshot_path"]))

            if not snapshot.is_dir():
                raise RuntimeError(
                    f"local model snapshot does not exist: {snapshot}"
                )

            _model = SentenceTransformer(
                str(snapshot),
                device=DEVICE,
                trust_remote_code=True,
                local_files_only=True,
                truncate_dim=int(manifest["dimensions"]),
            )
            _model.max_seq_length = int(manifest.get("max_length", 512))

        _model_last_used = time.monotonic()
        return _model


def unload_model() -> bool:
    global _model, _model_last_used

    with _model_lock:
        existed = _model is not None
        _model = None
        _model_last_used = 0.0

    if existed:
        try:
            import gc
            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass

    return existed


def maybe_unload_idle_model() -> None:
    if _model is None or _model_last_used <= 0:
        return

    if time.monotonic() - _model_last_used >= MODEL_IDLE_SECONDS:
        unload_model()


def encode_query(query: str) -> np.ndarray:
    global _model_last_used

    model = load_model()
    manifest = load_manifest()
    dimensions = int(manifest["dimensions"])

    vector = model.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0].astype(np.float32, copy=False)

    if vector.shape != (dimensions,):
        raise RuntimeError(
            f"unexpected query vector shape: {vector.shape}, expected {(dimensions,)}"
        )

    _model_last_used = time.monotonic()
    return vector


def load_passages() -> tuple[np.ndarray, list[dict[str, Any]]]:
    global _passage_matrix, _passage_rows

    with _passage_lock:
        if _passage_matrix is not None:
            return _passage_matrix, _passage_rows

        with open_ro(SEMANTIC_DB) as db:
            rows = db.execute(
                """
                SELECT
                    passage_id,
                    event_id,
                    source_path,
                    source_heading,
                    line_start,
                    line_end,
                    dimensions,
                    vector,
                    retrieval_text_sha256
                FROM passage_embeddings
                ORDER BY passage_id
                """
            ).fetchall()

        vectors: list[np.ndarray] = []
        metadata: list[dict[str, Any]] = []

        for row in rows:
            dimensions = int(row["dimensions"])
            vector = np.frombuffer(row["vector"], dtype="<f4")

            if vector.size != dimensions:
                raise RuntimeError(
                    f"{row['passage_id']}: vector has {vector.size} values, "
                    f"expected {dimensions}"
                )

            norm = float(np.linalg.norm(vector))
            if norm > 0:
                vector = vector / norm

            vectors.append(vector.astype(np.float32, copy=False))
            metadata.append(
                {
                    "passage_id": row["passage_id"],
                    "event_id": row["event_id"],
                    "source_path": row["source_path"],
                    "source_heading": row["source_heading"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                    "retrieval_text_sha256": row["retrieval_text_sha256"],
                }
            )

        if not vectors:
            raise RuntimeError("semantic database contains no passage vectors")

        _passage_matrix = np.stack(vectors)
        _passage_rows = metadata
        return _passage_matrix, _passage_rows


def safe_fts_query(text: str) -> str:
    tokens = re.findall(r"[\w가-힣]+", text, flags=re.UNICODE)
    tokens = [token for token in tokens if token]

    if not tokens:
        raise HTTPException(status_code=400, detail="query has no searchable tokens")

    # OR improves recall for natural-language questions.
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

def exact_event_search(query: str) -> list[dict[str, Any]]:
    if not query.startswith("evt_"):
        return []

    with open_ro(RESEARCH_DB) as db:
        row = db.execute(
            """
            SELECT
                event_id,
                date,
                event_type,
                status,
                project,
                workstream,
                summary,
                source_path,
                source_heading,
                source_sha256,
                line_start,
                line_end,
                requires_human_review,
                raw_json
            FROM events
            WHERE event_id = ?
            """,
            (query,),
        ).fetchone()

    if row is None:
        return []

    return [
        {
            "kind": "event",
            "event_id": row["event_id"],
            "date": row["date"],
            "event_type": row["event_type"],
            "status": row["status"],
            "project": row["project"],
            "workstream": row["workstream"],
            "summary": row["summary"],
            "path": row["source_path"],
            "heading": row["source_heading"],
            "start_line": row["line_start"],
            "end_line": row["line_end"],
            "source_sha256": row["source_sha256"],
            "requires_human_review": bool(row["requires_human_review"]),
            "raw_json": (
                json.loads(row["raw_json"])
                if row["raw_json"]
                else None
            ),
            "lexical_score": 1.0,
            "semantic_score": 0.0,
        }
    ]

def lexical_search(request: SearchRequest, candidate_limit: int) -> list[dict[str, Any]]:
    filters = []
    parameters: list[Any] = [safe_fts_query(request.query)]

    if request.project:
        filters.append("e.project = ?")
        parameters.append(request.project)
    if request.workstream:
        filters.append("e.workstream = ?")
        parameters.append(request.workstream)
    if request.status:
        filters.append("e.status = ?")
        parameters.append(request.status)

    filter_sql = ""
    if filters:
        filter_sql = " AND " + " AND ".join(filters)

    parameters.append(candidate_limit)

    with open_ro(RESEARCH_DB) as db:
        rows = db.execute(
            f"""
            SELECT
                e.event_id,
                e.date,
                e.event_type,
                e.status,
                e.project,
                e.workstream,
                e.summary,
                e.source_path,
                e.source_heading,
                e.source_sha256,
                e.line_start,
                e.line_end,
                e.requires_human_review,
                e.raw_json,
                bm25(event_fts, 1.0, 0.6, 0.4) AS bm25_raw
            FROM event_fts
            JOIN events e ON e.event_id = event_fts.event_id
            WHERE event_fts MATCH ?
            {filter_sql}
            ORDER BY bm25_raw ASC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

    if not rows:
        return []

    raw_scores = np.array(
        [-float(row["bm25_raw"]) for row in rows],
        dtype=np.float32,
    )
    minimum = float(raw_scores.min())
    maximum = float(raw_scores.max())

    if maximum > minimum:
        normalized = (raw_scores - minimum) / (maximum - minimum)
    else:
        normalized = np.ones_like(raw_scores)

    results = []
    for row, score in zip(rows, normalized, strict=True):
        results.append(
            {
                "kind": "event",
                "event_id": row["event_id"],
                "date": row["date"],
                "event_type": row["event_type"],
                "status": row["status"],
                "project": row["project"],
                "workstream": row["workstream"],
                "summary": row["summary"],
                "path": row["source_path"],
                "heading": row["source_heading"],
                "start_line": row["line_start"],
                "end_line": row["line_end"],
                "source_sha256": row["source_sha256"],
                "requires_human_review": bool(row["requires_human_review"]),
                "raw_json": (
                    json.loads(row["raw_json"])
                    if request.include_raw_json
                    else None
                ),
                "lexical_score": float(score),
                "semantic_score": 0.0,
            }
        )

    return results


def get_event_map(event_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not event_ids:
        return {}

    placeholders = ",".join("?" for _ in event_ids)

    with open_ro(RESEARCH_DB) as db:
        rows = db.execute(
            f"""
            SELECT
                event_id,
                date,
                event_type,
                status,
                project,
                workstream,
                summary,
                source_path,
                source_heading,
                source_sha256,
                line_start,
                line_end,
                requires_human_review,
                raw_json
            FROM events
            WHERE event_id IN ({placeholders})
            """,
            event_ids,
        ).fetchall()

    return {row["event_id"]: dict(row) for row in rows}


def semantic_search(
    request: SearchRequest,
    candidate_limit: int,
) -> list[dict[str, Any]]:
    matrix, passage_rows = load_passages()
    query_vector = encode_query(request.query)

    similarities = matrix @ query_vector
    candidate_limit = min(candidate_limit, similarities.size)

    if candidate_limit <= 0:
        return []

    indexes = np.argpartition(similarities, -candidate_limit)[-candidate_limit:]
    indexes = indexes[np.argsort(similarities[indexes])[::-1]]

    selected_passages = [passage_rows[int(index)] for index in indexes]
    events = get_event_map(
        list(dict.fromkeys(item["event_id"] for item in selected_passages))
    )

    results: list[dict[str, Any]] = []

    for index, passage in zip(indexes, selected_passages, strict=True):
        event = events.get(passage["event_id"])
        if not event:
            continue

        if request.project and event["project"] != request.project:
            continue
        if request.workstream and event["workstream"] != request.workstream:
            continue
        if request.status and event["status"] != request.status:
            continue

        results.append(
            {
                "kind": "passage",
                "passage_id": passage["passage_id"],
                "event_id": passage["event_id"],
                "date": event["date"],
                "event_type": event["event_type"],
                "status": event["status"],
                "project": event["project"],
                "workstream": event["workstream"],
                "summary": event["summary"],
                "path": passage["source_path"],
                "heading": passage["source_heading"],
                "start_line": passage["line_start"],
                "end_line": passage["line_end"],
                "source_sha256": event["source_sha256"],
                "retrieval_text_sha256": passage["retrieval_text_sha256"],
                "requires_human_review": bool(event["requires_human_review"]),
                "raw_json": (
                    json.loads(event["raw_json"])
                    if request.include_raw_json
                    else None
                ),
                "lexical_score": 0.0,
                "semantic_score": float(similarities[int(index)]),
            }
        )

    return results


def unique_event_results(
    items: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep the highest-ranked result for each event_id."""
    unique: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()

    for item in items:
        event_id = str(item["event_id"])
        if event_id in seen_event_ids:
            continue

        seen_event_ids.add(event_id)
        unique.append(item)

        if len(unique) >= limit:
            break

    return unique


def fuse_results(
    lexical: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    request: SearchRequest,
) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}

    for item in lexical:
        key = f"event:{item['event_id']}"
        combined[key] = dict(item)

    for item in semantic:
        key = f"passage:{item['passage_id']}"
        combined[key] = dict(item)

    # Propagate an event's lexical score to its passages.
    lexical_by_event = {
        item["event_id"]: item["lexical_score"] for item in lexical
    }

    for item in combined.values():
        item["lexical_score"] = max(
            float(item.get("lexical_score", 0.0)),
            float(lexical_by_event.get(item["event_id"], 0.0)),
        )

        semantic_score = float(item.get("semantic_score", 0.0))
        semantic_01 = max(0.0, min(1.0, (semantic_score + 1.0) / 2.0))

        item["hybrid_score"] = (
            request.lexical_weight * float(item["lexical_score"])
            + request.semantic_weight * semantic_01
        )

    ordered = sorted(
        combined.values(),
        key=lambda item: (
            item["hybrid_score"],
            item["semantic_score"],
            item["lexical_score"],
        ),
        reverse=True,
    )

    # Prefer passages over duplicate event-level results.
    passage_event_ids = {
        item["event_id"] for item in ordered if item["kind"] == "passage"
    }
    deduplicated = [
        item
        for item in ordered
        if not (
            item["kind"] == "event"
            and item["event_id"] in passage_event_ids
        )
    ]

    # Preserve the highest-ranked passage and its line range, while preventing
    # one large audit from occupying most of the diverse event result set.
    return unique_event_results(deduplicated, request.top_k)


def resolve_safe_path(relative_path: str) -> Path:
    supplied = Path(relative_path)

    if supplied.is_absolute() or ".." in supplied.parts:
        raise HTTPException(status_code=400, detail="unsafe path")

    lowered_parts = [part.lower() for part in supplied.parts]
    basename = supplied.name.lower()

    if basename in DENIED_NAMES:
        raise HTTPException(status_code=403, detail="access to this file is denied")

    if any(
        fragment in part
        for part in lowered_parts
        for fragment in DENIED_FRAGMENTS
    ):
        raise HTTPException(status_code=403, detail="access to this path is denied")

    resolved = (ROOT / supplied).resolve()

    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes project root") from exc

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    return resolved


def jsonl_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


@app.get("/health")
def health(_: None = Depends(require_api_key)) -> dict[str, Any]:
    maybe_unload_idle_model()

    return {
        "ok": True,
        "root": str(ROOT),
        "research_db": str(RESEARCH_DB),
        "semantic_db": str(SEMANTIC_DB),
        "model_loaded": _model is not None,
        "passage_cache_loaded": _passage_matrix is not None,
        "manifest": {
            key: load_manifest().get(key)
            for key in (
                "model",
                "model_revision",
                "dimensions",
                "event_count",
                "passage_count",
                "device",
            )
        },
    }


def request_with_query(
    request: SearchRequest,
    query: str,
) -> SearchRequest:
    """Return a SearchRequest clone with a different query string."""
    model_copy = getattr(request, "model_copy", None)

    if callable(model_copy):
        return model_copy(update={"query": query})

    # Compatibility with Pydantic v1.
    return request.copy(update={"query": query})


def merge_ranked_candidates(
    items: list[dict[str, Any]],
    key_name: str,
    score_name: str,
) -> list[dict[str, Any]]:
    """Keep the highest score for candidates returned by multiple queries."""
    merged: dict[str, dict[str, Any]] = {}

    for item in items:
        key = str(item[key_name])
        current = merged.get(key)

        if current is None or float(item[score_name]) > float(current[score_name]):
            merged[key] = item

    return sorted(
        merged.values(),
        key=lambda item: float(item[score_name]),
        reverse=True,
    )


@app.post("/search")
def search(
    request: SearchRequest,
    _: None = Depends(require_api_key),
) -> StreamingResponse:
    started = time.perf_counter()
    maybe_unload_idle_model()

    candidate_limit = min(max(request.top_k * 5, 50), 500)
    query_variants = build_query_variants(request.query)

    lexical_candidates: list[dict[str, Any]] = []
    semantic_candidates: list[dict[str, Any]] = []

    for variant in query_variants:
        variant_request = request_with_query(request, variant["query"])
        variant_weight = float(variant["weight"])

        if request.mode in {"lexical", "hybrid"}:
            variant_lexical = lexical_search(
                variant_request,
                candidate_limit,
            )
            for item in variant_lexical:
                item["lexical_score"] = (
                    float(item["lexical_score"]) * variant_weight
                )
            lexical_candidates.extend(variant_lexical)

        if request.mode in {"semantic", "hybrid"}:
            variant_semantic = semantic_search(
                variant_request,
                candidate_limit,
            )
            for item in variant_semantic:
                item["semantic_score"] = (
                    float(item["semantic_score"]) * variant_weight
                )
            semantic_candidates.extend(variant_semantic)

    exact = exact_event_search(request.query)

    if exact:
        return {
            "query": request.query,
            "mode": request.mode,
            "returned": len(exact),
            "results": exact,
        }

    lexical = merge_ranked_candidates(
        lexical_candidates,
        key_name="event_id",
        score_name="lexical_score",
    )
    semantic = merge_ranked_candidates(
        semantic_candidates,
        key_name="passage_id",
        score_name="semantic_score",
    )

    if request.mode == "lexical":
        final = lexical[: request.top_k]
        for item in final:
            item["hybrid_score"] = item["lexical_score"]
    elif request.mode == "semantic":
        final = unique_event_results(semantic, request.top_k)
        for item in final:
            item["hybrid_score"] = max(
                0.0,
                min(1.0, (item["semantic_score"] + 1.0) / 2.0),
            )
    else:
        final = fuse_results(lexical, semantic, request)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    def stream() -> Iterator[bytes]:
        for rank, result in enumerate(final, start=1):
            result["type"] = "result"
            result["rank"] = rank
            yield jsonl_line(result)

        yield jsonl_line(
            {
                "type": "meta",
                "query": request.query,
                "mode": request.mode,
                "query_expansion_count": max(0, len(query_variants) - 1),
                "query_variants": query_variants,
                "returned": len(final),
                "lexical_candidates": len(lexical),
                "semantic_candidates": len(semantic),
                "search_ms": elapsed_ms,
                "model_loaded": _model is not None,
            }
        )

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/fetch")
def fetch(
    request: FetchRequest,
    _: None = Depends(require_api_key),
) -> StreamingResponse:
    path = resolve_safe_path(request.path)

    start = max(1, request.start_line - request.context_lines)
    end = request.end_line + request.context_lines

    if end < start:
        raise HTTPException(status_code=400, detail="end_line precedes start_line")

    if end - start + 1 > MAX_FETCH_LINES:
        raise HTTPException(
            status_code=400,
            detail=f"maximum fetch size is {MAX_FETCH_LINES} lines",
        )

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    end = min(end, len(lines))
    selected = lines[start - 1 : end]
    content = "\n".join(
        f"{line_number:>7} | {text}"
        for line_number, text in enumerate(selected, start=start)
    )

    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    def stream() -> Iterator[bytes]:
        yield jsonl_line(
            {
                "type": "source",
                "path": str(path.relative_to(ROOT)),
                "start_line": start,
                "end_line": end,
                "total_lines": len(lines),
                "sha256": digest,
                "text": content,
            }
        )

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/admin/unload-model")
def admin_unload(_: None = Depends(require_api_key)) -> dict[str, Any]:
    return {
        "ok": True,
        "model_was_loaded": unload_model(),
        "model_loaded": False,
    }
