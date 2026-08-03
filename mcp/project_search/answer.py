"""Grounded local-LLM answer endpoint for Project Research Search."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .query_expansion import build_query_variants
from .server import (
    RESEARCH_DB,
    SEMANTIC_DB,
    SearchRequest,
    app,
    fuse_results,
    lexical_search,
    merge_ranked_candidates,
    open_ro,
    request_with_query,
    require_api_key,
    resolve_safe_path,
    semantic_search,
)


DEFAULT_MODEL = os.environ.get(
    "PROJECT_SEARCH_LLM_MODEL",
    "gemma4:26b-a4b-it-qat",
)
OLLAMA_URL = os.environ.get(
    "PROJECT_SEARCH_LLM_URL",
    "http://127.0.0.1:11434/api/chat",
)

LOW_VALUE_HEADINGS = {
    "sha256",
    "files",
    "artifacts",
    "environment",
    "commands",
    "metadata",
    "checksums",
}


class AnswerRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=8, ge=3, le=12)
    model: str | None = None


def jsonl(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def family_key(path_text: str) -> str:
    parts = Path(path_text).parts

    if len(parts) >= 3 and parts[0] == "results":
        # results/<workstream>/<experiment>/... 단위로 다양화한다.
        # workstream 전체를 하나로 묶으면 R1~R9 중 두 결과만 남는다.
        return "/".join(parts[:3])

    if len(parts) >= 2:
        return "/".join(parts[:-1])

    return path_text


# ANCHOR_EVENT_PASSAGE_RERANK_V1

ANCHOR_ROLE_WEIGHTS = {
    '"decision"': 18.0,
    '"phase_v5_eligible"': 18.0,
    '"recovered_seed_gates"': 16.0,
    '"all_three_recovered_seeds_passed"': 16.0,
    '"quality_gate"': 12.0,
    '"passed"': 5.0,
    '"candidate"': 8.0,
    '"technique_selected"': 14.0,
    '"span_selected"': 8.0,
    '"best_epoch"': 7.0,
    '"deltas"': 6.0,
    '"source"': 3.0,
    '"artifact"': 2.0,
}


def anchor_event_ids(
    query: str,
    candidates: list[dict[str, Any]],
) -> list[str]:
    """Resolve exact event IDs and Vn-Rn experiment anchors."""

    resolved: list[str] = []

    for event_id in re.findall(
        r"\bevt_[A-Za-z0-9_.-]+\b",
        query,
    ):
        resolved.append(event_id)

    round_keys = {
        f"v{version}_r{round_number}"
        for version, round_number in re.findall(
            r"\bV(\d+)[-_]?R(\d+)\b",
            query,
            flags=re.IGNORECASE,
        )
    }

    if round_keys:
        for item in candidates:
            event_id = str(item.get("event_id") or "")
            lowered = event_id.casefold()

            if any(key in lowered for key in round_keys):
                resolved.append(event_id)

    return list(dict.fromkeys(resolved))


def anchor_query_terms(query: str) -> list[str]:
    """Extract discriminative terms without promoting generic words."""

    terms: list[str] = []

    terms.extend(
        re.findall(
            r"-?\d+\.\d+",
            query,
        )
    )

    terms.extend(
        match.group(0)
        for match in re.finditer(
            r"\b(?:seed\s*[-_:]?\s*\d+|"
            r"V\d+[-_]R\d+|"
            r"Phase\s*V\d+)\b",
            query,
            flags=re.IGNORECASE,
        )
    )

    important_words = {
        "selected",
        "candidate",
        "decision",
        "quality",
        "gate",
        "failed",
        "passed",
        "blocked",
        "source",
        "lineage",
        "macro",
        "micro",
        "ece",
        "partial",
        "epoch",
        "최종",
        "선택",
        "실패",
        "통과",
        "차단",
        "결정",
        "근거",
    }

    lowered = query.casefold()

    for word in important_words:
        if word.casefold() in lowered:
            terms.append(word)

    return list(dict.fromkeys(term.casefold() for term in terms))


def passage_source_lines(
    item: dict[str, Any],
) -> tuple[list[str], int, int]:
    path = resolve_safe_path(str(item["path"]))

    lines = path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    start = max(1, int(item.get("start_line") or 1))
    end = min(
        len(lines),
        max(start, int(item.get("end_line") or start)),
    )

    return lines, start, end


def score_anchor_passage(
    query: str,
    item: dict[str, Any],
) -> tuple[float, int]:
    """Rank passages inside one canonical event by role and query fit."""

    lines, start, end = passage_source_lines(item)
    terms = anchor_query_terms(query)

    passage_lines = lines[start - 1 : end]
    content = "\n".join(passage_lines)
    lowered = content.casefold()

    score = 0.0

    for term in terms:
        if term not in lowered:
            continue

        if re.fullmatch(r"-?\d+\.\d+", term):
            score += 20.0
        elif any(character.isdigit() for character in term):
            score += 9.0
        else:
            score += 4.0

    for role, weight in ANCHOR_ROLE_WEIGHTS.items():
        if role in lowered:
            score += weight

    query_lower = query.casefold()

    asks_for_final = any(
        term in query_lower
        for term in (
            "selected",
            "final",
            "best",
            "decision",
            "최종",
            "선택",
            "결정",
        )
    )

    if asks_for_final:
        if '"technique_selected"' in lowered:
            score += 18.0
        if '"candidate"' in lowered:
            score += 10.0
        if '"decision"' in lowered:
            score += 12.0

        # Epoch history만 있고 최종 candidate/decision이 없는 passage는
        # 최종값 질문에서 낮춘다.
        if (
            '"history"' in lowered
            and '"candidate"' not in lowered
            and '"decision"' not in lowered
        ):
            score -= 8.0

    asks_for_gate = any(
        term in query_lower
        for term in (
            "gate",
            "blocked",
            "failed",
            "passed",
            "phase",
            "차단",
            "실패",
            "통과",
        )
    )

    if asks_for_gate:
        if '"quality_gate"' in lowered:
            score += 14.0
        if '"phase_v5_eligible"' in lowered:
            score += 20.0
        if '"recovered_seed_gates"' in lowered:
            score += 18.0

    # Hash/integrity 꼬리는 event/path 문자열 때문에 semantic score가
    # 높더라도 연구 질문의 핵심 근거로는 낮은 가치다.
    core_roles = (
        '"decision"',
        '"quality_gate"',
        '"candidate"',
        '"technique_selected"',
        '"phase_v5_eligible"',
    )

    if lowered.count("sha256") >= 3 and not any(
        role in lowered for role in core_roles
    ):
        score -= 24.0

    if (
        '"integrity"' in lowered
        and not any(role in lowered for role in core_roles)
    ):
        score -= 10.0

    # Evidence를 어느 줄 중심으로 읽을지도 함께 결정한다.
    best_line = start
    best_line_score = float("-inf")

    for line_number in range(start, end + 1):
        line_lower = lines[line_number - 1].casefold()
        line_score = 0.0

        for term in terms:
            if term in line_lower:
                line_score += (
                    15.0
                    if re.fullmatch(r"-?\d+\.\d+", term)
                    else 5.0
                )

        for role, weight in ANCHOR_ROLE_WEIGHTS.items():
            if role in line_lower:
                line_score += weight

        if line_score > best_line_score:
            best_line_score = line_score
            best_line = line_number

    return score, best_line


def expand_anchor_event_passages(
    query: str,
    candidates: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Retrieve and rerank every passage belonging to an anchored event."""

    event_ids = anchor_event_ids(query, candidates)

    if not event_ids:
        return []

    templates: dict[str, dict[str, Any]] = {}

    for item in candidates:
        event_id = str(item.get("event_id") or "")

        if event_id not in event_ids:
            continue

        current = templates.get(event_id)

        if (
            current is None
            or float(item.get("hybrid_score", 0.0))
            > float(current.get("hybrid_score", 0.0))
        ):
            templates[event_id] = item

    expanded: list[dict[str, Any]] = []

    with open_ro(SEMANTIC_DB) as db:
        for event_id in event_ids:
            template = templates.get(event_id)

            if template is None:
                continue

            rows = db.execute(
                """
                SELECT
                    passage_id,
                    event_id,
                    source_path,
                    source_heading,
                    line_start,
                    line_end,
                    retrieval_text_sha256
                FROM passage_embeddings
                WHERE event_id = ?
                ORDER BY line_start
                """,
                (event_id,),
            ).fetchall()

            event_items: list[dict[str, Any]] = []

            for row in rows:
                item = dict(template)
                item.update(
                    {
                        "kind": "passage",
                        "passage_id": row["passage_id"],
                        "event_id": row["event_id"],
                        "path": row["source_path"],
                        "heading": row["source_heading"],
                        "start_line": row["line_start"],
                        "end_line": row["line_end"],
                        "retrieval_text_sha256": (
                            row["retrieval_text_sha256"]
                        ),
                        "_anchor_expanded": True,
                    }
                )

                role_score, focus_line = score_anchor_passage(
                    query,
                    item,
                )

                item["_anchor_role_score"] = role_score
                item["_focus_line"] = focus_line

                # 화면상의 score도 내부 재랭킹을 반영하되 기존 검색
                # 점수와 혼동되지 않도록 작은 보정값만 더한다.
                item["hybrid_score"] = (
                    float(template.get("hybrid_score", 0.0))
                    + role_score / 1000.0
                )

                event_items.append(item)

            event_items.sort(
                key=lambda item: (
                    float(item.get("_anchor_role_score", 0.0)),
                    float(item.get("hybrid_score", 0.0)),
                    -int(item.get("start_line") or 0),
                ),
                reverse=True,
            )

            expanded.extend(event_items[:limit])

    return expanded



# CANONICAL_EVENT_TIMELINE_BUNDLE_V1

TIMELINE_QUERY_MARKERS = (
    "timeline",
    "time-series",
    "initial plan",
    "round",
    "초기 계획",
    "시간순",
    "타임라인",
    "과정",
    "흐름",
    "계보",
    "r1",
    "r2",
    "r3",
    "r4",
    "r5",
    "r6",
    "r7",
    "r8",
    "r9",
)

VOCAB_QUERY_MARKERS = (
    "vocab",
    "vocabulary",
    "embedding",
    "64k",
    "어휘",
    "임베딩",
)


def is_vocabulary_timeline_query(query: str) -> bool:
    lowered = query.casefold()

    has_timeline = any(
        marker in lowered
        for marker in TIMELINE_QUERY_MARKERS
    )
    has_vocab = any(
        marker in lowered
        for marker in VOCAB_QUERY_MARKERS
    )

    return has_timeline and has_vocab


def event_stage_key(event: dict[str, Any]) -> tuple[Any, ...]:
    event_id = str(event.get("event_id") or "")
    date = str(event.get("date") or "")

    version_match = re.search(
        r"_vocab_v(\d+)",
        event_id,
        flags=re.IGNORECASE,
    )
    round_match = re.search(
        r"_v\d+_r(\d+)",
        event_id,
        flags=re.IGNORECASE,
    )

    version = (
        int(version_match.group(1))
        if version_match
        else -1
    )

    if round_match:
        round_number = int(round_match.group(1))
    elif "recovery_smoke" in event_id:
        round_number = 0
    else:
        round_number = -1

    return (
        date,
        version,
        round_number,
        event_id,
    )


def as_text_items(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return [
            str(item)
            for item in value
            if str(item).strip()
        ]

    if isinstance(value, dict):
        return [
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
            )
        ]

    return [str(value)]



def canonical_observed_highlights(
    event: dict[str, Any],
) -> str:
    """Extract final candidate metrics and gates from canonical observed."""

    observed = event.get("observed")

    if not isinstance(observed, dict):
        return ""

    metric_keys = (
        "technique_macro_f1",
        "technique_micro_f1",
        "span_exact_f1",
        "span_partial_f1",
        "ece",
    )

    def metrics(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        return {
            key: value[key]
            for key in metric_keys
            if key in value
        }

    highlights: dict[str, Any] = {}

    top_candidate = metrics(observed.get("candidate"))

    if top_candidate:
        highlights["candidate"] = top_candidate

    if isinstance(observed.get("quality_gate"), dict):
        gate = observed["quality_gate"]
        highlights["quality_gate"] = {
            key: gate[key]
            for key in ("passed", "gates")
            if key in gate
        }

    if isinstance(observed.get("candidate_vs_full_deltas"), dict):
        highlights["candidate_vs_full_deltas"] = {
            key: observed["candidate_vs_full_deltas"][key]
            for key in metric_keys
            if key in observed["candidate_vs_full_deltas"]
        }

    for seed_key in ("seed_41", "seed_42", "seed_43"):
        seed = observed.get(seed_key)

        if not isinstance(seed, dict):
            continue

        seed_summary: dict[str, Any] = {}

        for key in (
            "source_event",
            "best_epoch",
            "quality_gate_passed",
            "failed_gates",
        ):
            if key in seed:
                seed_summary[key] = seed[key]

        seed_candidate = metrics(seed.get("candidate"))

        if seed_candidate:
            seed_summary["candidate"] = seed_candidate

        for delta_key in (
            "deltas_vs_full",
            "deltas",
            "candidate_vs_full_deltas",
        ):
            delta_value = seed.get(delta_key)

            if not isinstance(delta_value, dict):
                continue

            selected_deltas = {
                key: delta_value[key]
                for key in metric_keys
                if key in delta_value
            }

            if selected_deltas:
                seed_summary[delta_key] = selected_deltas

        if seed_summary:
            highlights[seed_key] = seed_summary

    observed_decision = observed.get("decision")

    if isinstance(observed_decision, dict):
        highlights["observed_decision"] = observed_decision

    calibration = observed.get("calibration")

    if isinstance(calibration, dict):
        compact_calibration: dict[str, Any] = {}

        for arm, value in calibration.items():
            if not isinstance(value, dict):
                continue

            selected = {
                key: value[key]
                for key in (
                    "macro_f1",
                    "micro_f1",
                    "mean_average_precision",
                    "ece",
                )
                if key in value
            }

            if selected:
                compact_calibration[str(arm)] = selected

        if compact_calibration:
            highlights["calibration"] = compact_calibration

    if not highlights:
        return ""

    return json.dumps(
        highlights,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def compact_canonical_event(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "unknown")
    summary = str(event.get("summary") or "").strip()
    status = str(event.get("status") or "")
    event_type = str(event.get("event_type") or "")
    date = str(event.get("date") or "")

    interpretation = as_text_items(
        event.get("interpretation")
    )
    decision = as_text_items(event.get("decision"))
    uncertainty = as_text_items(event.get("uncertainty"))

    source = event.get("source") or {}

    if isinstance(source, dict):
        source_path = (
            source.get("source_path")
            or source.get("path")
            or ""
        )
    else:
        source_path = str(source)

    rows = [
        f"EVENT: {event_id}",
        f"DATE: {date}",
        f"TYPE/STATUS: {event_type} / {status}",
    ]

    observed_highlights = canonical_observed_highlights(event)

    if summary:
        rows.append(f"SUMMARY: {summary}")

    if observed_highlights:
        rows.append(
            "OBSERVED HIGHLIGHTS: "
            + observed_highlights
        )

    if interpretation:
        rows.append("INTERPRETATION:")
        rows.extend(
            f"- {item}"
            for item in interpretation[:2]
        )

    if decision:
        rows.append("DECISION:")
        rows.extend(
            f"- {item}"
            for item in decision[:4]
        )

    if uncertainty:
        rows.append("UNCERTAINTY:")
        rows.append(f"- {uncertainty[0]}")

    if source_path:
        rows.append(f"SOURCE ARTIFACT: {source_path}")

    return "\n".join(rows)


def load_vocabulary_canonical_events() -> list[dict[str, Any]]:
    with open_ro(RESEARCH_DB) as db:
        rows = db.execute(
            """
            SELECT
                event_id,
                date,
                event_type,
                status,
                summary,
                source_path,
                raw_json
            FROM events
            WHERE
                event_id LIKE '%_vocab_%'
                OR source_path LIKE
                    'results/vocabulary_embedding/%'
                OR lower(summary) LIKE '%v64k%'
                OR lower(summary) LIKE '%64k vocabulary%'
            ORDER BY date, event_id
            """
        ).fetchall()

    events: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        event_id = str(row["event_id"])

        if event_id in seen:
            continue

        try:
            event = json.loads(row["raw_json"])
        except (TypeError, json.JSONDecodeError):
            event = {
                "event_id": event_id,
                "date": row["date"],
                "event_type": row["event_type"],
                "status": row["status"],
                "summary": row["summary"],
                "source": {
                    "path": row["source_path"],
                },
            }

        source = event.get("source") or {}

        if isinstance(source, dict):
            source_path = str(
                source.get("source_path")
                or source.get("path")
                or ""
            )
        else:
            source_path = str(source)

        summary = str(event.get("summary") or "")
        lowered = (
            event_id
            + " "
            + source_path
            + " "
            + summary
        ).casefold()

        if not any(
            marker in lowered
            for marker in (
                "vocab",
                "vocabulary_embedding",
                "v64k",
                "64k",
            )
        ):
            continue

        seen.add(event_id)
        events.append(event)

    events.sort(key=event_stage_key)

    # CANONICAL_TIMELINE_CONTEXT_FIX_V2
    # 시간순 질문에서는 중간 round가 핵심 인과 연결이므로
    # first-N + last-N 방식으로 이벤트를 버리지 않는다.
    return events


def build_canonical_timeline_bundles(
    query: str,
) -> list[dict[str, Any]]:
    if not is_vocabulary_timeline_query(query):
        return []

    events = load_vocabulary_canonical_events()

    if not events:
        return []

    # source slot을 과도하게 차지하지 않으면서도 각 round의
    # 목적→해석→결정이 보존되도록 최대 네 event씩 묶는다.
    groups = [
        events[index : index + 4]
        for index in range(0, len(events), 4)
    ]

    # 모든 중간 round bundle을 유지한다.
    bundles: list[dict[str, Any]] = []

    for bundle_index, group in enumerate(groups, start=1):
        first_event = str(group[0].get("event_id") or "")
        last_event = str(group[-1].get("event_id") or "")
        first_date = str(group[0].get("date") or "unknown")

        content_rows = [
            "AUTHORITY: canonical research event JSONL",
            (
                "These entries preserve event summary, interpretation, "
                "decision, uncertainty and linked source artifact."
            ),
            "",
        ]

        for event in group:
            content_rows.append(
                compact_canonical_event(event)
            )
            content_rows.append(
                "\n" + "-" * 72 + "\n"
            )

        content = "\n".join(content_rows).rstrip()

        bundles.append(
            {
                "kind": "inline_canonical_timeline",
                "event_id": (
                    f"canonical_timeline_bundle_{bundle_index}"
                ),
                "date": first_date,
                "event_type": "canonical_timeline",
                "status": "canonical",
                "project": "paper_qwen_intent_classifier",
                "workstream": "vocabulary_embedding",
                "summary": (
                    f"Canonical vocabulary timeline: "
                    f"{first_event} through {last_event}"
                ),
                "path": (
                    f"research-events/daily/"
                    f"{first_date}/events.jsonl"
                ),
                "heading": (
                    f"Canonical vocabulary timeline "
                    f"{bundle_index}"
                ),
                "start_line": 1,
                "end_line": 1,
                "lexical_score": 1.0,
                "semantic_score": 1.0,
                "hybrid_score": 10.0 - bundle_index / 100.0,
                "_timeline_bundle": True,
                "_inline_evidence": content,
                "_timeline_event_ids": [
                    str(event.get("event_id") or "")
                    for event in group
                ],
            }
        )

    return bundles



# CLAIM_EVIDENCE_PLANNER_V1

AUDIT_QUERY_TERMS = (
    "audit",
    "hash",
    "sha256",
    "tensor",
    "shape",
    "dtype",
    "tokenizer",
    "token id",
    "mapping",
    "remap",
    "equivalent_under_map",
    "embedding",
    "vocab",
    "vocabulary",
    "vocab size",
    "semantic preservation",
    "semantic loss",
    "meaning preservation",
    "meaning loss",
    "의미 손실",
    "의미 보존",
    "의미 정보",
    "표현력 보존",
    "표현력 손실",
    "pad token",
    "eos token",
    "artifact",
    "state hash",
    "무결성",
    "해시",
    "텐서",
    "형상",
    "토크나이저",
    "토큰 id",
    "매핑",
    "리맵",
    "구조 검증",
)

METRIC_QUERY_TERMS = (
    "f1",
    "macro",
    "micro",
    "ece",
    "metric",
    "score",
    "quality gate",
    "performance",
    "성능",
    "품질",
    "지표",
    "게이트",
)


def classify_research_query(query: str) -> str:
    lowered = query.casefold()

    version_refs = re.findall(
        r"(?<![A-Za-z0-9_])V\d+",
        query,
        flags=re.IGNORECASE,
    )
    round_refs = re.findall(
        r"(?<![A-Za-z0-9_])R\d+",
        query,
        flags=re.IGNORECASE,
    )

    timeline_language = any(
        marker in lowered
        for marker in (
            "timeline",
            "time line",
            "round",
            "타임라인",
            "시간순",
            "과정",
            "흐름",
            "계보",
            "왜",
            "이유",
            "다음",
            "넘어",
            "진행",
            "실패",
        )
    )

    distinct_rounds = {
        ref.casefold()
        for ref in round_refs
    }

    explicit_round_chain = (
        timeline_language
        and (
            len(distinct_rounds) >= 2
            or (
                bool(version_refs)
                and bool(round_refs)
            )
        )
    )

    if (
        is_vocabulary_timeline_query(query)
        or explicit_round_chain
    ):
        return "timeline"

    audit_score = sum(
        1
        for term in AUDIT_QUERY_TERMS
        if term.casefold() in lowered
    )
    metric_score = sum(
        1
        for term in METRIC_QUERY_TERMS
        if term.casefold() in lowered
    )

    if audit_score >= metric_score and audit_score > 0:
        return "audit"

    if metric_score > 0:
        return "metric"

    return "hybrid"

def audit_query_terms(query: str) -> list[str]:
    terms: list[str] = []

    terms.extend(
        match.casefold()
        for match in re.findall(
            r"""
            evt_[A-Za-z0-9_.-]+
            |V\d+[-_]R\d+
            |seed\s*[-_:]?\s*\d+
            |-?\d+\.\d+
            |[A-Fa-f0-9]{16,64}
            """,
            query,
            flags=re.IGNORECASE | re.VERBOSE,
        )
    )

    concept_groups = {
        "tokenizer": (
            "tokenizer",
            "토크나이저",
        ),
        "mapping": (
            "mapping",
            "mapped",
            "remap",
            "equivalent_under_map",
            "source_ids",
            "output_ids",
            "매핑",
            "리맵",
        ),
        "embedding": (
            "embedding",
            "dense gather",
            "retained row",
            "임베딩",
        ),
        "tensor": (
            "tensor",
            "shape",
            "dtype",
            "finite",
            "identical",
            "텐서",
            "형상",
        ),
        "hash": (
            "hash",
            "sha256",
            "state_sha256",
            "해시",
        ),
        "config": (
            "vocab_size",
            "pad_token_id",
            "eos_token_id",
            "config",
            "설정",
        ),
        "metric": (
            "f1",
            "macro",
            "micro",
            "ece",
            "quality_gate",
            "metric",
            "성능",
            "품질",
        ),
        "decision": (
            "decision",
            "passed",
            "failed",
            "blocked",
            "phase_v5",
            "결정",
            "통과",
            "실패",
            "차단",
        ),
    }

    lowered = query.casefold()

    for canonical, aliases in concept_groups.items():
        if any(alias.casefold() in lowered for alias in aliases):
            terms.append(canonical)

    return list(dict.fromkeys(terms))


def score_audit_passage(
    query: str,
    item: dict[str, Any],
) -> tuple[float, int]:
    lines, start, end = passage_source_lines(item)
    passage = "\n".join(lines[start - 1 : end])
    lowered = passage.casefold()
    query_lower = query.casefold()
    terms = audit_query_terms(query)

    score = 0.0

    exact_terms = re.findall(
        r"""
        evt_[A-Za-z0-9_.-]+
        |V\d+[-_]R\d+
        |seed\s*[-_:]?\s*\d+
        |-?\d+\.\d+
        |[A-Fa-f0-9]{16,64}
        """,
        query,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for term in exact_terms:
        if term.casefold() in lowered:
            score += 28.0

    role_groups = {
        "tokenizer": (
            '"tokenizer"',
            "tokenizer.json",
            "tokenizer_config.json",
            "source_ids",
            "mapped_ids",
            "output_ids",
            "equivalent_under_map",
        ),
        "mapping": (
            "equivalent_under_map",
            "source_ids",
            "mapped_ids",
            "output_ids",
            "dense gather",
            "id_map",
        ),
        "embedding": (
            '"embedding"',
            "embedding exact dense gather",
            "retained_rows",
            "source_shape",
            "output_shape",
        ),
        "tensor": (
            "tensor keys",
            "non-embedding tensors",
            "all output tensors finite",
            '"shape"',
            '"dtype"',
            '"finite"',
        ),
        "hash": (
            "sha256",
            "state_sha256",
            "source_sha256",
        ),
        "config": (
            "vocab_size",
            "pad_token_id",
            "eos_token_id",
        ),
        "metric": (
            "macro_f1",
            "micro_f1",
            "span_partial_f1",
            "official_si_f1",
            '"ece"',
            "quality_gate",
        ),
        "decision": (
            '"decision"',
            "phase_v5_eligible",
            "failed_gate",
            '"passed"',
        ),
    }

    for term in terms:
        for marker in role_groups.get(term, (term,)):
            if marker.casefold() in lowered:
                score += 9.0

    if any(
        marker in query_lower
        for marker in (
            "가능",
            "불가능",
            "주장",
            "증명",
            "evidence",
            "근거",
            "claim",
        )
    ):
        for marker, weight in (
            ('"gates"', 12.0),
            ('"boundaries"', 12.0),
            ('"decision"', 10.0),
            ('"uncertainty"', 10.0),
            ("quality_claimed", 12.0),
            ("equivalent_under_map", 12.0),
            ("exact dense gather", 12.0),
            ("finite", 7.0),
            ("identical", 7.0),
        ):
            if marker in lowered:
                score += weight

    asks_hash = any(
        marker in query_lower
        for marker in (
            "hash",
            "sha",
            "해시",
        )
    )

    if not asks_hash and lowered.count("sha256") >= 4:
        substantive = (
            "equivalent_under_map",
            "technique_selected",
            "quality_gate",
            '"decision"',
            "vocab_size",
            "embedding",
            "tensor",
        )

        if not any(marker in lowered for marker in substantive):
            score -= 22.0

    best_line = start
    best_line_score = float("-inf")

    for line_number in range(start, end + 1):
        line_lower = lines[line_number - 1].casefold()
        line_score = 0.0

        for term in exact_terms:
            if term.casefold() in line_lower:
                line_score += 24.0

        for term in terms:
            for marker in role_groups.get(term, (term,)):
                if marker.casefold() in line_lower:
                    line_score += 6.0

        if line_score > best_line_score:
            best_line_score = line_score
            best_line = line_number

    return score, best_line


def expand_audit_passages(
    query: str,
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    templates: dict[str, dict[str, Any]] = {}

    for item in candidates:
        event_id = str(item.get("event_id") or "")
        path_text = str(item.get("path") or "")

        if not event_id or not path_text:
            continue

        lowered = (
            event_id
            + " "
            + path_text
            + " "
            + str(item.get("heading") or "")
        ).casefold()

        if not (
            path_text.endswith("audit.json")
            or "/results/" in "/" + path_text
            or "audit" in lowered
        ):
            continue

        current = templates.get(event_id)

        if (
            current is None
            or float(item.get("hybrid_score", 0.0))
            > float(current.get("hybrid_score", 0.0))
        ):
            templates[event_id] = item

    ordered_templates = sorted(
        templates.values(),
        key=lambda item: float(item.get("hybrid_score", 0.0)),
        reverse=True,
    )[:5]

    expanded: list[dict[str, Any]] = []

    with open_ro(SEMANTIC_DB) as db:
        for template in ordered_templates:
            event_id = str(template["event_id"])

            rows = db.execute(
                """
                SELECT
                    passage_id,
                    event_id,
                    source_path,
                    source_heading,
                    line_start,
                    line_end,
                    retrieval_text_sha256
                FROM passage_embeddings
                WHERE event_id = ?
                ORDER BY line_start
                """,
                (event_id,),
            ).fetchall()

            event_items: list[dict[str, Any]] = []

            for row in rows:
                item = dict(template)
                item.update(
                    {
                        "kind": "passage",
                        "passage_id": row["passage_id"],
                        "event_id": row["event_id"],
                        "path": row["source_path"],
                        "heading": row["source_heading"],
                        "start_line": row["line_start"],
                        "end_line": row["line_end"],
                        "retrieval_text_sha256": (
                            row["retrieval_text_sha256"]
                        ),
                        "_audit_expanded": True,
                    }
                )

                role_score, focus_line = score_audit_passage(
                    query,
                    item,
                )

                item["_audit_role_score"] = role_score
                item["_focus_line"] = focus_line
                item["hybrid_score"] = (
                    float(template.get("hybrid_score", 0.0))
                    + role_score / 1000.0
                )

                event_items.append(item)

            event_items.sort(
                key=lambda item: (
                    float(item.get("_audit_role_score", 0.0)),
                    float(item.get("hybrid_score", 0.0)),
                    -int(item.get("start_line") or 0),
                ),
                reverse=True,
            )

            expanded.extend(event_items[:3])

    expanded.sort(
        key=lambda item: (
            float(item.get("_audit_role_score", 0.0)),
            float(item.get("hybrid_score", 0.0)),
        ),
        reverse=True,
    )

    return expanded[:limit]


def classify_evidence_category(
    item: dict[str, Any],
    evidence: str,
) -> str:
    if item.get("_canonical_metric_bundle"):
        return "metric"

    if item.get("_timeline_bundle"):
        return "timeline"

    path_text = str(item.get("path") or "").casefold()
    lowered = evidence.casefold()

    if (
        '"decision"' in lowered
        or "\ndecision:" in lowered
        or "phase_v5_eligible" in lowered
        or "failed_gate" in lowered
    ):
        return "decision"

    if any(
        marker in lowered
        for marker in (
            "macro_f1",
            "micro_f1",
            "span_partial_f1",
            "official_si_f1",
            '"ece"',
            "quality_gate",
        )
    ):
        return "metric"

    if (
        path_text.endswith("audit.json")
        or any(
            marker in lowered
            for marker in (
                "sha256",
                "tensor",
                "tokenizer",
                "vocab_size",
                "equivalent_under_map",
                "dense gather",
            )
        )
    ):
        return "audit"

    return "general"


def detect_evidence_role(
    item: dict[str, Any],
    evidence: str,
) -> str:
    lowered = evidence.casefold()

    if (
        "\nuncertainty:" in lowered
        and not any(
            marker in lowered
            for marker in (
                "\ndecision:",
                "\ninterpretation:",
                "\nobserved",
            )
        )
    ):
        return "limitation"

    if (
        "\ndecision:" in lowered
        or '"decision"' in lowered
        or "phase_v5_eligible" in lowered
    ):
        return "decision"

    if "\ninterpretation:" in lowered:
        return "analysis"

    if (
        "\nobserved" in lowered
        or any(
            marker in lowered
            for marker in (
                "macro_f1",
                "micro_f1",
                "equivalent_under_map",
                "dense gather",
                '"finite"',
            )
        )
    ):
        return "measurement"

    if any(
        marker in lowered
        for marker in (
            "sha256",
            "tensor",
            "tokenizer",
            "vocab_size",
        )
    ):
        return "verification"

    return "unknown"


def split_event_evidence(
    item: dict[str, Any],
    evidence: str,
) -> list[tuple[str, str]]:
    if not item.get("_timeline_bundle"):
        return [
            (
                str(item.get("event_id") or "unknown"),
                evidence,
            )
        ]

    content = (
        evidence.split("Content:\n", 1)[1]
        if "Content:\n" in evidence
        else evidence
    )

    matches = list(
        re.finditer(
            r"(?m)^EVENT:\s*(\S+)\s*$",
            content,
        )
    )

    if not matches:
        return [
            (
                str(item.get("event_id") or "unknown"),
                evidence,
            )
        ]

    sections: list[tuple[str, str]] = []

    for index, match in enumerate(matches):
        start = match.start()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(content)
        )
        sections.append(
            (
                match.group(1),
                content[start:end],
            )
        )

    return sections


def detect_evidence_conflicts(
    entries: list[tuple[dict[str, Any], str]],
) -> dict[str, Any]:
    passed_events: list[str] = []
    failed_events: list[str] = []
    mixed_events: list[str] = []

    pass_patterns = (
        r'quality_gate_passed["\']?\s*[:=]\s*true',
        r'"passed"\s*:\s*true',
        r"\bpassed all\b",
        r"\bgate pass(?:ed)?\b",
        r"\b통과\b",
    )
    fail_patterns = (
        r'quality_gate_passed["\']?\s*[:=]\s*false',
        r'"passed"\s*:\s*false',
        r"\bfailed_gate\b",
        r'phase_v5_eligible["\']?\s*[:=]\s*false',
        r"\bblocked\b",
        r"\b실패\b",
        r"\b차단\b",
    )

    for item, evidence in entries:
        for event_id, section in split_event_evidence(
            item,
            evidence,
        ):
            lowered = section.casefold()

            passed = any(
                re.search(pattern, lowered)
                for pattern in pass_patterns
            )
            failed = any(
                re.search(pattern, lowered)
                for pattern in fail_patterns
            )

            if passed and failed:
                mixed_events.append(event_id)
            elif passed:
                passed_events.append(event_id)
            elif failed:
                failed_events.append(event_id)

    passed_events = list(dict.fromkeys(passed_events))
    failed_events = list(dict.fromkeys(failed_events))
    mixed_events = list(dict.fromkeys(mixed_events))

    return {
        "conflict": bool(
            (passed_events and failed_events)
            or mixed_events
        ),
        "passed_events": passed_events,
        "failed_events": failed_events,
        "mixed_events": mixed_events,
        "resolution_rule": (
            "전체 결론에는 더 늦은 multi-seed/confirmation/decision "
            "event를 우선하되, 앞선 pilot pass는 해당 단계의 "
            "국소 결과로 보존한다."
        ),
    }


def build_evidence_map(
    entries: list[tuple[dict[str, Any], str]],
) -> str:
    rows: list[str] = []

    for source_id, (item, evidence) in enumerate(
        entries,
        start=1,
    ):
        category = classify_evidence_category(
            item,
            evidence,
        )
        role = detect_evidence_role(
            item,
            evidence,
        )

        event_ids = (
            item.get("_timeline_event_ids")
            or [item.get("event_id")]
        )
        event_ids = [
            str(event_id)
            for event_id in event_ids
            if event_id
        ]

        rows.extend(
            [
                f"[{source_id}]",
                f"category: {category}",
                f"role: {role}",
                f"events: {', '.join(event_ids)}",
                f"path: {item.get('path')}",
                "",
            ]
        )

    return "\n".join(rows).rstrip()


def build_query_coverage_check(
    query: str,
    evidence_text: str,
) -> dict[str, Any]:
    query_lower = query.casefold()
    evidence_lower = evidence_text.casefold()

    anchors: list[str] = []

    anchors.extend(
        match.casefold()
        for match in re.findall(
            r"""
            evt_[A-Za-z0-9_.-]+
            |V\d+[-_]R\d+
            |seed\s*[-_:]?\s*\d+
            |-?\d+\.\d+
            """,
            query,
            flags=re.IGNORECASE | re.VERBOSE,
        )
    )

    concept_aliases = {
        "tokenizer": (
            "tokenizer",
            "토크나이저",
        ),
        "mapping": (
            "mapping",
            "mapped",
            "remap",
            "equivalent_under_map",
            "매핑",
            "리맵",
        ),
        "embedding": (
            "embedding",
            "임베딩",
        ),
        "tensor": (
            "tensor",
            "shape",
            "dtype",
            "텐서",
            "형상",
        ),
        "hash": (
            "hash",
            "sha256",
            "해시",
        ),
        "metric": (
            "f1",
            "macro",
            "micro",
            "ece",
            "성능",
            "품질",
        ),
        "decision": (
            "decision",
            "passed",
            "failed",
            "blocked",
            "결정",
            "통과",
            "실패",
            "차단",
        ),
    }

    missing: list[str] = []

    for anchor in dict.fromkeys(anchors):
        if anchor not in evidence_lower:
            missing.append(anchor)

    for concept, aliases in concept_aliases.items():
        asked = any(
            alias.casefold() in query_lower
            for alias in aliases
        )

        if not asked:
            continue

        found = any(
            alias.casefold() in evidence_lower
            for alias in aliases
        )

        if not found:
            missing.append(concept)

    return {
        "status": (
            "possible_retrieval_gap"
            if missing
            else "no_obvious_query_anchor_gap"
        ),
        "missing_from_current_evidence": list(
            dict.fromkeys(missing)
        ),
        "meaning": (
            "누락 표시는 현재 EVIDENCE 기준이며 저장소 전체 부재를 "
            "뜻하지 않는다."
        ),
    }



# CANONICAL_METRIC_ANCHOR_V1

# CANONICAL_METRIC_CONTEXT_COMPACTION_V2

def build_canonical_metric_anchor_results(
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    요청 round의 canonical event와 직접 참조된 provenance event만 반환한다.

    예:
    R9 -> source_event=R8

    seed 이름만 같다는 이유로 R1 pilot 같은 무관한 event를 추가하지 않는다.
    """
    if classify_research_query(query) != "metric":
        return []

    round_refs = list(
        dict.fromkeys(
            match.casefold()
            for match in re.findall(
                r"(?<![A-Za-z0-9_])R\d+",
                query,
                flags=re.IGNORECASE,
            )
        )
    )

    if not round_refs:
        return []

    version_refs = list(
        dict.fromkeys(
            match.casefold()
            for match in re.findall(
                r"(?<![A-Za-z0-9_])V\d+",
                query,
                flags=re.IGNORECASE,
            )
        )
    )

    events = load_vocabulary_canonical_events()

    event_by_id: dict[str, dict[str, Any]] = {}

    for event in events:
        event_id = str(
            event.get("event_id")
            or event.get("id")
            or ""
        )

        if event_id:
            event_by_id[event_id] = event

    primary_events: list[dict[str, Any]] = []

    for event_id, event in event_by_id.items():
        lowered = event_id.casefold()

        round_match = any(
            f"_{round_ref}_" in lowered
            or lowered.endswith(f"_{round_ref}")
            for round_ref in round_refs
        )

        if not round_match:
            continue

        if version_refs:
            version_match = any(
                f"_{version_ref}_" in lowered
                or lowered.endswith(f"_{version_ref}")
                for version_ref in version_refs
            )

            if not version_match:
                continue

        primary_events.append(event)

    provenance_key_tokens = (
        "source_event",
        "source_event_id",
        "parent_event",
        "derived_from",
        "provenance_event",
    )

    def collect_direct_provenance_ids(
        value: Any,
    ) -> list[str]:
        found: list[str] = []

        if isinstance(value, dict):
            for key, child in value.items():
                key_lower = str(key).casefold()

                if any(
                    token in key_lower
                    for token in provenance_key_tokens
                ):
                    if isinstance(child, str):
                        found.extend(
                            re.findall(
                                r"evt_[A-Za-z0-9_.-]+",
                                child,
                                flags=re.IGNORECASE,
                            )
                        )

                    elif isinstance(child, list):
                        for item in child:
                            if isinstance(item, str):
                                found.extend(
                                    re.findall(
                                        r"evt_[A-Za-z0-9_.-]+",
                                        item,
                                        flags=re.IGNORECASE,
                                    )
                                )

                    elif isinstance(child, dict):
                        found.extend(
                            collect_direct_provenance_ids(
                                child
                            )
                        )

                if isinstance(child, (dict, list)):
                    found.extend(
                        collect_direct_provenance_ids(
                            child
                        )
                    )

        elif isinstance(value, list):
            for child in value:
                found.extend(
                    collect_direct_provenance_ids(
                        child
                    )
                )

        return list(dict.fromkeys(found))

    selected_events: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add_event(
        event: dict[str, Any],
    ) -> None:
        event_id = str(
            event.get("event_id")
            or event.get("id")
            or ""
        )

        if (
            not event_id
            or event_id in selected_ids
            or len(selected_events) >= limit
        ):
            return

        selected_ids.add(event_id)
        selected_events.append(event)

    for event in primary_events:
        add_event(event)

    # primary event가 직접 가리키는 provenance만 추가한다.
    # linked event의 linked event까지 재귀적으로 확장하지 않는다.
    for event in primary_events:
        for referenced_id in collect_direct_provenance_ids(
            event
        ):
            linked_event = event_by_id.get(
                referenced_id
            )

            if linked_event is not None:
                add_event(linked_event)

    scalar_key_tokens = (
        "event_id",
        "source_event",
        "source_event_id",
        "seed",
        "epoch",
        "macro",
        "micro",
        "partial",
        "official_si",
        "ece",
        "f1",
        "quality_gate_passed",
        "failed_gate",
        "phase_v5",
        "all_three",
        "passed",
        "eligible",
        "selected",
    )

    container_key_tokens = (
        "metrics",
        "quality_gate",
        "gates",
        "deltas",
        "thresholds",
        "failed_gates",
        "candidate_vs_full_deltas",
        "candidate_metrics",
        "selected_metrics",
    )

    def key_matches(
        key: str,
        tokens: tuple[str, ...],
    ) -> bool:
        lowered = key.casefold()

        return any(
            token in lowered
            for token in tokens
        )

    def prune_metric_payload(
        value: Any,
        key: str = "",
        preserve_descendants: bool = False,
    ) -> Any:
        preserve_here = (
            preserve_descendants
            or key_matches(
                key,
                container_key_tokens,
            )
        )

        if isinstance(value, dict):
            output: dict[str, Any] = {}

            for child_key, child_value in value.items():
                child = prune_metric_payload(
                    child_value,
                    str(child_key),
                    preserve_here,
                )

                if child is not None:
                    output[str(child_key)] = child

            return output or None

        if isinstance(value, list):
            output_list = []

            for child in value:
                if isinstance(child, (dict, list)):
                    pruned_child = prune_metric_payload(
                        child,
                        key,
                        preserve_here,
                    )

                    if pruned_child is not None:
                        output_list.append(
                            pruned_child
                        )

                elif (
                    preserve_here
                    or key_matches(
                        key,
                        scalar_key_tokens,
                    )
                ):
                    output_list.append(child)

            return output_list or None

        if (
            preserve_here
            or key_matches(
                key,
                scalar_key_tokens,
            )
        ):
            return value

        return None

    results: list[dict[str, Any]] = []

    for index, event in enumerate(selected_events):
        event_id = str(
            event.get("event_id")
            or event.get("id")
            or ""
        )

        compact = compact_canonical_event(event)
        metric_payload = (
            prune_metric_payload(event)
            or {
                "event_id": event_id,
            }
        )

        evidence = (
            compact
            + "\n\nCANONICAL METRIC PAYLOAD\n"
            + json.dumps(
                metric_payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )

        results.append(
            {
                "kind": "canonical_metric_bundle",
                "event_id": event_id,
                "path": "research-events/events.jsonl",
                "heading": (
                    "canonical metric event "
                    f"{event_id}"
                ),
                "start_line": 1,
                "end_line": evidence.count("\n") + 1,
                "hybrid_score": 1000000.0 - index,
                "_inline_evidence": evidence,
                "_canonical_metric_bundle": True,
                "_timeline_event_ids": [event_id],
            }
        )

    return results

def needs_latest_retrieval(query: str) -> bool:
    keywords = [
        "최근",
        "최신",
        "현재",
        "지금",
        "근황",
        "상태",
        "어디까지",
        "진행 상황",
        "뭐 했",
        "무엇을 했",
    ]

    return any(k in query for k in keywords)

def run_hybrid_search(query: str, top_k: int) -> list[dict[str, Any]]:
    # 먼저 넓게 검색한 뒤 family 다양화로 압축한다.
    internal_top_k = max(top_k * 5, 40)

    # 짧은 질문, 코드 식별자, 실험 ID, 경로 및 오류 문자열은
    # 의미 확장보다 정확 문자열 검색을 우선한다.
    identifier_candidates = re.findall(
        r"(?:[A-Za-z][A-Za-z0-9_.:/-]{1,127}"
        r"|\d+(?:\.\d+)+)",
        query,
    )

    # seed/source/passed/blocked 같은 일반 영단어를 별도 고가중치
    # 검색어로 만들지 않는다. 실험 ID, seed41, V4-R9, 경로,
    # 버전 또는 수치처럼 구별력이 있는 토큰만 유지한다.
    identifiers = list(
        dict.fromkeys(
            token
            for token in identifier_candidates
            if (
                any(character.isdigit() for character in token)
                or any(
                    character in token
                    for character in "/\\._:-"
                )
            )
        )
    )

    lexical_first = (
        len(query.split()) <= 5
        or bool(identifiers)
        or any(character.isdigit() for character in query)
        or any(character in query for character in "/\\._:-")
    )

    lexical_weight = 0.85 if lexical_first else 0.35
    semantic_weight = 1.0 - lexical_weight

    request = SearchRequest(
        query=query,
        mode="hybrid",
        top_k=internal_top_k,
        lexical_weight=lexical_weight,
        semantic_weight=semantic_weight,
    )

    candidate_limit = min(max(internal_top_k * 5, 100), 500)

    if lexical_first:
        # 짧은 질의를 일반 개념으로 과도하게 확장하지 않는다.
        variants = [{"query": query, "weight": 1.0}]

        for identifier in identifiers:
            if identifier.casefold() == query.casefold():
                continue

            if identifier.startswith("evt_") or "/" in identifier:
                identifier_weight = 1.2
            else:
                identifier_weight = 0.9

            variants.append(
                {
                    "query": identifier,
                    "weight": identifier_weight,
                }
            )
    else:
        variants = build_query_variants(query)

    lexical_candidates: list[dict[str, Any]] = []
    semantic_candidates: list[dict[str, Any]] = []

    for variant in variants:
        variant_request = request_with_query(request, variant["query"])
        variant_weight = float(variant["weight"])

        lexical = lexical_search(variant_request, candidate_limit)
        for item in lexical:
            item["lexical_score"] = (
                float(item["lexical_score"]) * variant_weight
            )
        lexical_candidates.extend(lexical)

        semantic = semantic_search(variant_request, candidate_limit)
        for item in semantic:
            item["semantic_score"] = (
                float(item["semantic_score"]) * variant_weight
            )
        semantic_candidates.extend(semantic)

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

    fused = fuse_results(lexical, semantic, request)

    timeline_bundles = build_canonical_timeline_bundles(
        query
    )

    # 타임라인 질문에서 raw audit passage를 뒤에 섞으면
    # smoke loss/hash passage가 context를 소모하고 canonical 근거를
    # 왼쪽에서 밀어낸다. Canonical event bundle만 사용한다.
    if timeline_bundles:
        return timeline_bundles[:top_k]

    query_mode = classify_research_query(query)

    canonical_metric_results = (
        build_canonical_metric_anchor_results(
            query,
            limit=3,
        )
        if query_mode == "metric"
        else []
    )

    # Canonical metric event가 요청 수치를 완결하면 raw audit 및
    # 무관한 metric 문서를 추가하지 않는다.
    if (
        query_mode == "metric"
        and canonical_metric_results
    ):
        return canonical_metric_results[:top_k]

    audit_passages: list[dict[str, Any]] = []

    if query_mode in {"audit", "metric"}:
        audit_passages = expand_audit_passages(
            query,
            lexical + semantic + fused,
            limit=min(max(top_k, 4), 8),
        )

    remaining_slots = max(
        1,
        top_k - len(audit_passages),
    )

    anchor_limit = max(
        1,
        min(5, remaining_slots),
    )

    anchor_passages = expand_anchor_event_passages(
        query,
        lexical + semantic + fused,
        limit=anchor_limit,
    )

    priority_passages = (
        canonical_metric_results
        + audit_passages
        + anchor_passages
    )

    priority_passage_ids = {
        str(item.get("passage_id"))
        for item in priority_passages
        if item.get("passage_id")
    }

    fused = priority_passages + [
        item
        for item in fused
        if str(item.get("passage_id"))
        not in priority_passage_ids
    ]

    selected: list[dict[str, Any]] = []
    family_counts: dict[str, int] = defaultdict(int)
    event_counts: dict[str, int] = defaultdict(int)

    for item in fused:
        if item.get("_timeline_bundle"):
            selected.append(item)

            if len(selected) >= top_k:
                break

            continue

        heading = str(item.get("heading") or "").strip()
        path_text = str(item.get("path") or "").strip()

        if not path_text:
            continue

        if heading.lower() in LOW_VALUE_HEADINGS:
            continue

        family = family_key(path_text)
        event_id = str(item.get("event_id") or "")

        # 하나의 긴 audit에서도 selected metrics, quality gate,
        # lineage, decision이 서로 다른 passage에 존재할 수 있다.
        is_priority = bool(
            item.get("_anchor_expanded")
            or item.get("_audit_expanded")
            or item.get("_canonical_metric_bundle")
        )

        event_limit = 5 if is_priority else 4

        if event_counts[event_id] >= event_limit:
            continue

        family_limit = (
            5
            if is_priority
            else 4
            if path_text.startswith("results/")
            else 2
        )

        if family_counts[family] >= family_limit:
            continue

        event_counts[event_id] += 1
        family_counts[family] += 1
        selected.append(item)

        if len(selected) >= top_k:
            break

    return selected


def read_evidence(
    item: dict[str, Any],
    source_id: int,
    context_lines: int = 5,
    max_chars: int = 2600,
) -> tuple[dict[str, Any], str]:
    path_text = str(item["path"])
    inline_evidence = item.get("_inline_evidence")

    if inline_evidence is not None:
        content = str(inline_evidence)

        inline_max_chars = max(max_chars, 12000)

        if len(content) > inline_max_chars:
            content = (
                content[:inline_max_chars]
                + "\n…[canonical bundle truncated]"
            )

        source = {
            "id": source_id,
            "event_id": item["event_id"],
            "heading": (
                item.get("heading")
                or item.get("summary")
                or "Canonical event timeline"
            ),
            "path": path_text,
            "start_line": 1,
            "end_line": 1,
            "hybrid_score": float(
                item.get("hybrid_score", 0.0)
            ),
            "event_ids": item.get(
                "_timeline_event_ids",
                [],
            ),
        }

        evidence = (
            f"[{source_id}]\n"
            f"Title: {source['heading']}\n"
            f"Path: {path_text}\n"
            f"Canonical event bundle\n"
            f"Content:\n{content}"
        )

        return source, evidence

    path = resolve_safe_path(path_text)

    lines = path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    result_start = max(1, int(item.get("start_line") or 1))
    result_end = max(result_start, int(item.get("end_line") or result_start))

    focus_line = item.get("_focus_line")

    if focus_line is not None:
        focus = max(
            result_start,
            min(result_end, int(focus_line)),
        )
        focus_radius = 18

        start = max(
            1,
            max(result_start, focus - focus_radius)
            - context_lines,
        )
        end = min(
            len(lines),
            min(result_end, focus + focus_radius)
            + context_lines,
        )
    else:
        start = max(1, result_start - context_lines)
        end = min(len(lines), result_end + context_lines)

    content = "\n".join(
        f"{line_number}: {lines[line_number - 1]}"
        for line_number in range(start, end + 1)
    )

    if len(content) > max_chars:
        content = content[:max_chars] + "\n…[truncated]"

    source = {
        "id": source_id,
        "event_id": item["event_id"],
        "heading": item.get("heading") or item.get("summary") or path.name,
        "path": path_text,
        "start_line": result_start,
        "end_line": result_end,
        "hybrid_score": float(item.get("hybrid_score", 0.0)),
    }

    evidence = (
        f"[{source_id}]\n"
        f"Title: {source['heading']}\n"
        f"Path: {path_text}\n"
        f"Relevant lines: {result_start}-{result_end}\n"
        f"Content:\n{content}"
    )

    return source, evidence


def build_grounded_prompt(
    query: str,
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    sources: list[dict[str, Any]] = []
    evidence_blocks: list[str] = []
    evidence_entries: list[
        tuple[dict[str, Any], str]
    ] = []

    for source_id, result in enumerate(
        results,
        start=1,
    ):
        try:
            source, evidence = read_evidence(
                result,
                source_id,
            )
        except (OSError, ValueError):
            continue

        category = classify_evidence_category(
            result,
            evidence,
        )
        role = detect_evidence_role(
            result,
            evidence,
        )

        source["category"] = category
        source["role"] = role

        sources.append(source)
        evidence_blocks.append(evidence)
        evidence_entries.append(
            (result, evidence)
        )

    evidence_text = "\n\n---\n\n".join(
        evidence_blocks
    )
    query_mode = classify_research_query(query)
    evidence_map = build_evidence_map(
        evidence_entries
    )
    conflict_info = detect_evidence_conflicts(
        evidence_entries
    )
    coverage_info = build_query_coverage_check(
        query,
        evidence_text,
    )

    conflict_text = json.dumps(
        conflict_info,
        ensure_ascii=False,
        indent=2,
    )
    coverage_text = json.dumps(
        coverage_info,
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""QUESTION
{query}

RETRIEVAL MODE
{query_mode}

EVIDENCE MAP
{evidence_map}

CONFLICT CHECK
{conflict_text}

RETRIEVAL COVERAGE CHECK
{coverage_text}

EVIDENCE
{evidence_text}

INSTRUCTIONS
- EVIDENCE에 포함된 정보만 사용한다.
- 중요한 사실 주장마다 반드시 [번호] 인용을 붙인다.
- 인용 번호는 위 EVIDENCE의 번호와 정확히 일치해야 한다.
- 질문의 전제가 근거와 충돌하면 먼저 전제를 교정한다.
- 사실, 해석, 불확실성 또는 주장 한계를 구분한다.
- 여러 실험의 시간적·인과적 흐름이 있으면 원인 → 진단 → 복구 → 검증 순으로 정리한다.
- Canonical event bundle이 있으면 raw audit의 중간 epoch 수치보다 SUMMARY, OBSERVED HIGHLIGHTS, INTERPRETATION, DECISION을 우선한다.
- canonical metric event가 있으면 seed 식별과 최종 selected metric은 raw audit의 잘린 passage보다 canonical OBSERVED HIGHLIGHTS를 우선한다.
- bundle 안에 요구된 round가 존재하면 해당 round가 누락됐다고 주장하지 않는다.
- audit/verification 근거만으로 downstream 품질, 속도, RSS 또는 의미 보존을 주장하지 않는다.
- 선택된 embedding row의 exact gather와 전체 vocabulary 의미 표현력 보존을 구분한다.
- metric 또는 canonical observed/decision 근거가 없으면 성능 판단을 하지 않는다.
- completed, passed, failed_gate, blocked를 하나의 성공 결과로 합치지 않는다.
- pilot pass 뒤에 multi-seed 또는 confirmatory failure가 있으면 전체 결론에는 후속 검증을 우선한다.
- seed가 다른 결과를 하나의 모델 상태처럼 합치지 않는다.
- observation, interpretation, decision, uncertainty를 구분한다.
- RETRIEVAL COVERAGE CHECK의 누락은 현재 근거 기준 검색 공백일 뿐 저장소 전체에 정보가 없다는 뜻이 아니다.
- 현재 근거에서 찾지 못한 정보를 저장소 전체에 없다고 단정하지 않는다.
- 근거가 부족한 부분은 추측하지 말고 현재 EVIDENCE에서 확인되지 않았다고 표현한다.
- 한국어로 답한다.
"""

    return sources, prompt


@app.post("/answer")
def answer(
    request: AnswerRequest,
    _: None = Depends(require_api_key),
) -> StreamingResponse:

    if needs_latest_retrieval(request.query):
        results = run_hybrid_search(request.query, request.top_k)
    else:
        results = run_hybrid_search(request.query, request.top_k)

    sources, user_prompt = build_grounded_prompt(
        request.query,
        results,
    )
    model = request.model or DEFAULT_MODEL

    ollama_payload = {
        "model": model,
        "stream": True,
        "think": True,
        "keep_alive": "30m",
        "messages": [
            {
                "role": "system",
                "content": (
                    "너는 개인 연구 저장소의 근거 합성기다. "
                    "제공된 근거 밖의 사실을 만들지 않는다. "
                    "모든 인용 번호는 실제 근거와 일치해야 한다. "
                    "사고 과정은 thinking 필드에서 수행하되, "
                    "반드시 content 필드에 최종 한국어 답변을 작성한다. "
                    "서로 다른 experiment ID의 수치나 결론을 결합하지 않는다. "
                    "필수 엔티티의 근거가 빠졌다면 누락을 명시한다."
                ),
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "options": {
            "num_ctx": 16384,
            "num_predict": 1536,  # PLANNER_FINALIZER_V2
            "temperature": 0.25,
            "top_p": 0.9,
            "seed": 20260722,
        },
    }

    def stream() -> Iterator[bytes]:
        yield jsonl(
            {
                "type": "sources",
                "model": model,
                "sources": sources,
            }
        )

        encoded = json.dumps(
            ollama_payload,
            ensure_ascii=False,
        ).encode("utf-8")

        http_request = urllib.request.Request(
            OLLAMA_URL,
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                http_request,
                timeout=600,
            ) as response:
                final_stats: dict[str, Any] = {}
                emitted_content = False
                thinking_active = False

                # THINKING_FINALIZER_FALLBACK_V1
                # 1차 reasoning이 최종 content 없이 끝날 경우,
                # reasoning 일부를 2차 finalizer에 넘긴다.
                thinking_chunks: list[str] = []
                draft_chunks: list[str] = []

                for raw_line in response:
                    if not raw_line.strip():
                        continue

                    record = json.loads(raw_line)
                    message = record.get("message", {})
                    thinking = message.get("thinking") or ""
                    content = message.get("content") or ""

                    # Do not expose the raw reasoning trace. Only tell the UI
                    # whether the model is currently reasoning.
                    if thinking:
                        thinking_chunks.append(thinking)

                        if not thinking_active:
                            thinking_active = True
                            yield jsonl(
                                {
                                    "type": "thinking",
                                    "active": True,
                                }
                            )

                        # Stream the model's reasoning separately from the
                        # final answer. The frontend keeps it collapsed unless
                        # the user explicitly opens the reasoning panel.
                        yield jsonl(
                            {
                                "type": "thinking_delta",
                                "content": thinking,
                            }
                        )

                    if content:
                        # 1차 호출은 planner 전용이다. 여기서 생성된 content는
                        # 사용자에게 직접 보내지 않고 finalizer용 초안으로 쓴다.
                        draft_chunks.append(content)

                    if record.get("done"):
                        final_stats = {
                            "prompt_eval_count": record.get(
                                "prompt_eval_count"
                            ),
                            "eval_count": record.get("eval_count"),
                            "load_duration": record.get("load_duration"),
                            "prompt_eval_duration": record.get(
                                "prompt_eval_duration"
                            ),
                            "eval_duration": record.get("eval_duration"),
                            "total_duration": record.get("total_duration"),
                            "done_reason": record.get("done_reason"),
                        }

                # 1차 planner가 끝나도 reasoning 표시를 유지한다.
                # 2차 finalizer의 첫 content가 도착할 때 닫는다.
                if not emitted_content:
                    if not thinking_active:
                        thinking_active = True
                        yield jsonl(
                            {
                                "type": "thinking",
                                "active": True,
                            }
                        )

                    yield jsonl(
                        {
                            "type": "thinking_delta",
                            "content": (
                                "\n\n[1차 근거 분석 완료 — "
                                "최종 답변 구성 중]\n"
                            ),
                        }
                    )
                    # 1차 호출이 reasoning만 생성하고 content 없이 끝난 경우,
                    # 동일 근거와 reasoning 핵심을 이용해 최종 답변만 생성한다.
                    reasoning_text = "".join(
                        thinking_chunks
                    ).strip()
                    draft_text = "".join(draft_chunks).strip()

                    analysis_parts: list[str] = []

                    if reasoning_text:
                        analysis_parts.append(
                            "REASONING MEMO:\n" + reasoning_text
                        )

                    if draft_text:
                        analysis_parts.append(
                            "FIRST-PASS DRAFT:\n" + draft_text
                        )

                    analysis_text = "\n\n".join(analysis_parts)

                    # 원 EVIDENCE가 이미 context 대부분을 차지하므로
                    # planner 메모는 결론부 중심의 2200자로 제한한다.
                    if len(analysis_text) > 2200:
                        analysis_text = (
                            analysis_text[:300]
                            + "\n\n...[planner 메모 중간 생략]...\n\n"
                            + analysis_text[-1900:]
                        )

                    fallback_messages = list(
                        ollama_payload["messages"]
                    )

                    if analysis_text:
                        fallback_messages.append(
                            {
                                "role": "assistant",
                                "content": (
                                    "내부 연구 분석 메모다. "
                                    "사용자에게 이 메모 자체를 출력하지 말고 "
                                    "최종 답변 작성에만 사용하라.\n\n"
                                    + analysis_text
                                ),
                            }
                        )

                    fallback_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "이제 사고 과정을 반복하지 말고 "
                                "최종 한국어 답변만 작성하라. "
                                "EVIDENCE 밖의 사실을 만들지 말고, "
                                "중요한 사실마다 [번호] 인용을 붙여라. "
                                "서로 다른 experiment ID의 수치를 섞지 말라. "
                                "요구된 seed, round, metric 또는 decision의 "
                                "근거가 빠졌다면 그 누락을 명시하라."
                            ),
                        }
                    )

                    fallback_payload = dict(ollama_payload)
                    fallback_payload["think"] = False
                    fallback_payload["messages"] = fallback_messages

                    fallback_options = dict(
                        ollama_payload.get("options", {})
                    )
                    fallback_options["temperature"] = 0.1
                    fallback_options["top_p"] = 0.85
                    fallback_options["num_predict"] = 3072
                    fallback_payload["options"] = fallback_options

                    fallback_encoded = json.dumps(
                        fallback_payload,
                        ensure_ascii=False,
                    ).encode("utf-8")

                    fallback_request = urllib.request.Request(
                        OLLAMA_URL,
                        data=fallback_encoded,
                        headers={
                            "Content-Type": "application/json"
                        },
                        method="POST",
                    )

                    with urllib.request.urlopen(
                        fallback_request,
                        timeout=600,
                    ) as fallback_response:
                        for fallback_raw_line in fallback_response:
                            if not fallback_raw_line.strip():
                                continue

                            fallback_record = json.loads(
                                fallback_raw_line
                            )
                            fallback_message = (
                                fallback_record.get("message", {})
                            )
                            fallback_content = (
                                fallback_message.get("content") or ""
                            )

                            if fallback_content:
                                if thinking_active:
                                    thinking_active = False
                                    yield jsonl(
                                        {
                                            "type": "thinking",
                                            "active": False,
                                        }
                                    )

                                emitted_content = True
                                yield jsonl(
                                    {
                                        "type": "delta",
                                        "content": fallback_content,
                                    }
                                )

                            if fallback_record.get("done"):
                                final_stats = {
                                    "prompt_eval_count": (
                                        fallback_record.get(
                                            "prompt_eval_count"
                                        )
                                    ),
                                    "eval_count": fallback_record.get(
                                        "eval_count"
                                    ),
                                    "load_duration": fallback_record.get(
                                        "load_duration"
                                    ),
                                    "prompt_eval_duration": (
                                        fallback_record.get(
                                            "prompt_eval_duration"
                                        )
                                    ),
                                    "eval_duration": (
                                        fallback_record.get(
                                            "eval_duration"
                                        )
                                    ),
                                    "total_duration": (
                                        fallback_record.get(
                                            "total_duration"
                                        )
                                    ),
                                    "done_reason": fallback_record.get(
                                        "done_reason"
                                    ),
                                    "fallback_finalization": True,
                                }

                    if thinking_active:
                        thinking_active = False
                        yield jsonl(
                            {
                                "type": "thinking",
                                "active": False,
                            }
                        )

                    if not emitted_content:
                        yield jsonl(
                            {
                                "type": "error",
                                "message": (
                                    "thinking 분석과 2차 finalization 모두 "
                                    "최종 answer content를 반환하지 않았습니다."
                                ),
                            }
                        )

                yield jsonl(
                    {
                        "type": "done",
                        "model": model,
                        "stats": final_stats,
                    }
                )

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            yield jsonl(
                {
                    "type": "error",
                    "message": f"Ollama HTTP {exc.code}: {detail}",
                }
            )
        except Exception as exc:
            yield jsonl(
                {
                    "type": "error",
                    "message": str(exc),
                }
            )

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
    )
