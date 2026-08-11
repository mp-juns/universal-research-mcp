#!/usr/bin/env python3
"""Build line-addressable retrieval records from the local reference bundle."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REFERENCE_EVENT_TYPE = "reference_document"
REFERENCE_PROJECT = "external-reference"
REFERENCE_WORKSTREAM = "literature"
REFERENCE_SORT_DATE = "0001-01-01"
EXTRACTOR_VERSION = "3"
OCR_CACHE_RELATIVE = Path("research-events/index/reference-ocr")
TEXT_EXTENSIONS = {
    "",
    ".bib",
    ".cfg",
    ".csv",
    ".html",
    ".ini",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class ReferenceCorpus:
    events: list[dict[str, Any]]
    fingerprint: str
    manifest_count: int
    auxiliary_count: int
    extracted_count: int
    reused_count: int
    pdf_count: int
    pdf_page_count: int
    sparse_pdf_page_count: int
    ocr_pdf_page_count: int
    extraction_error_count: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_inside(project_root: Path, path_text: str) -> Path:
    candidate = (project_root / path_text).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Reference path escapes project root: {path_text}") from exc
    return candidate


def _slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("_.-").lower()
    if not slug:
        raise ValueError(f"Reference key cannot form an identifier: {value!r}")
    return slug


def _bib_entries(text: str) -> dict[str, str]:
    """Return raw BibTeX entries keyed by citation key."""
    entries: dict[str, str] = {}
    pattern = re.compile(r"@[A-Za-z]+\s*\{\s*([^,\s]+)\s*,")
    for match in pattern.finditer(text):
        opening = text.find("{", match.start())
        depth = 0
        escaped = False
        for index in range(opening, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    entries[match.group(1)] = text[opening + 1 : index]
                    break
    return entries


def _bib_field(entry: str, field: str) -> str | None:
    match = re.search(rf"(?im)^\s*{re.escape(field)}\s*=\s*", entry)
    if match is None:
        return None
    start = match.end()
    if start >= len(entry):
        return None
    opener = entry[start]
    if opener == "{":
        depth = 0
        escaped = False
        for index in range(start, len(entry)):
            char = entry[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return entry[start + 1 : index]
    if opener == '"':
        escaped = False
        for index in range(start + 1, len(entry)):
            char = entry[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                return entry[start + 1 : index]
    return entry[start:].split(",", 1)[0].strip()


def load_bib_titles(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    entries = _bib_entries(path.read_text(encoding="utf-8", errors="replace"))
    titles: dict[str, str] = {}
    for key, entry in entries.items():
        title = _bib_field(entry, "title")
        if not title:
            continue
        title = re.sub(r"[{}]", "", title)
        title = re.sub(r"\s+", " ", title).strip()
        titles[key] = title
    return titles


def _artifact_fields(record: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    nested = record.get("pdf")
    artifact = nested if isinstance(nested, dict) else record
    path_text = artifact.get("path")
    status = artifact.get("status")
    expected_sha256 = artifact.get("sha256")
    source_url = record.get("source_url") or artifact.get("url") or record.get("url")
    return path_text, status, expected_sha256, source_url


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in text.splitlines()]
    normalized: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            normalized.append(line)
            blank = False
        elif not blank:
            normalized.append("")
            blank = True
    return "\n".join(normalized).strip()


def _load_ocr_cache(
    project_root: Path,
    source_sha256: str | None,
) -> tuple[dict[int, str], str | None, str | None]:
    if not source_sha256:
        return {}, None, None
    cache_path = project_root / OCR_CACHE_RELATIVE / f"{source_sha256}.json"
    if not cache_path.is_file():
        return {}, None, None
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    if payload.get("source_sha256") != source_sha256:
        raise ValueError(f"OCR cache source hash mismatch: {cache_path}")
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, dict):
        raise ValueError(f"OCR cache has no pages object: {cache_path}")
    pages: dict[int, str] = {}
    for page_number, record in raw_pages.items():
        if not isinstance(record, dict):
            raise ValueError(f"OCR page record must be an object: {cache_path}:{page_number}")
        text = _normalize_text(str(record.get("text") or ""))
        if text:
            pages[int(page_number)] = text
    return pages, sha256_file(cache_path), str(payload.get("engine") or "not recorded")


def _extract_pdf(
    path: Path,
    ocr_pages: dict[int, str] | None = None,
    ocr_engine: str | None = None,
) -> tuple[str, dict[str, Any]]:
    from pypdf import PdfReader

    ocr_pages = ocr_pages or {}
    reader = PdfReader(str(path), strict=False)
    pages: list[str] = []
    sparse_pages: list[int] = []
    applied_ocr_pages: list[int] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = _normalize_text(page.extract_text() or "")
        character_count = len(re.sub(r"\s+", "", text))
        if character_count < 40:
            sparse_pages.append(page_number)
            sparse_note = f"[Sparse embedded text: {character_count} non-space characters]"
            text = f"{sparse_note}\n{text}" if text else sparse_note
            ocr_text = ocr_pages.get(page_number)
            if ocr_text:
                applied_ocr_pages.append(page_number)
                text += f"\n[OCR text]\n{ocr_text}"
        pages.append(f"[Page {page_number}]\n{text}" if text else f"[Page {page_number}]")
    return "\n\n".join(pages), {
        "pdf": True,
        "page_count": len(reader.pages),
        "sparse_pages": sparse_pages,
        "ocr_pages": applied_ocr_pages,
        "ocr_engine": ocr_engine if applied_ocr_pages else None,
    }


def _extract_text(
    path: Path,
    ocr_pages: dict[int, str] | None = None,
    ocr_engine: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if path.suffix.lower() == ".pdf":
        return _extract_pdf(path, ocr_pages, ocr_engine)
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return "", {"pdf": False, "binary": True}
    return _normalize_text(raw.decode("utf-8", errors="replace")), {"pdf": False}


def _write_if_changed(path: Path, content: str) -> None:
    payload = content.encode("utf-8")
    if path.is_file() and path.read_bytes() == payload:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _cached_extraction_stats(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    page_matches = list(re.finditer(r"(?m)^\[Page (\d+)\]$", text))
    page_numbers = [int(match.group(1)) for match in page_matches]
    sparse_pages: list[int] = []
    for index, match in enumerate(page_matches):
        end = page_matches[index + 1].start() if index + 1 < len(page_matches) else len(text)
        if "[Sparse embedded text:" in text[match.end() : end]:
            sparse_pages.append(int(match.group(1)))
    ocr_match = re.search(r"(?m)^OCR pages: (\[[^\n]*\])$", text[:8192])
    ocr_pages = json.loads(ocr_match.group(1)) if ocr_match else []
    engine_match = re.search(r"(?m)^OCR engine: ([^\n]+)$", text[:8192])
    return {
        "pdf": bool(page_numbers),
        "page_count": max(page_numbers, default=0),
        "sparse_pages": sparse_pages,
        "ocr_pages": ocr_pages,
        "ocr_engine": engine_match.group(1) if engine_match else None,
        "error": "Extraction error:" in text[:4096],
    }


def _header_line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        count = 0
        for line in handle:
            if not line.strip():
                break
            count += 1
    return max(1, count)


def _registered_paths(records: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for record in records:
        for value in (record, record.get("pdf"), record.get("bibtex")):
            if isinstance(value, dict) and value.get("path"):
                paths.add(str(value["path"]))
    return paths


def _auxiliary_records(project_root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references_root = project_root / "references"
    registered = _registered_paths(records)
    auxiliary: list[dict[str, Any]] = []
    for path in sorted(candidate for candidate in references_root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(project_root).as_posix()
        if relative in registered or ".part" in path.suffixes:
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS | {".pdf"}:
            continue
        relative_reference = path.relative_to(references_root).as_posix()
        auxiliary.append(
            {
                "id": f"local:{relative_reference}",
                "key": f"local_{_slug(relative_reference)}",
                "kind": "local_reference_document",
                "role": "local reference-bundle metadata, citation ledger, survey, or unregistered artifact",
                "path": relative,
                "status": "existing",
                "title": relative_reference,
                "license_note": "local project document; authorship and upstream rights remain as recorded in the file",
            }
        )
    return auxiliary


def build_reference_corpus(
    project_root: Path,
    manifest_path: Path | None = None,
    output_root: Path | None = None,
) -> ReferenceCorpus:
    project_root = project_root.resolve()
    manifest_path = (manifest_path or project_root / "references/manifest.json").resolve()
    output_root = (output_root or project_root / "research-events/index/references").resolve()
    if not manifest_path.is_file():
        return ReferenceCorpus([], hashlib.sha256(b"no-reference-manifest").hexdigest(), 0, 0, 0, 0, 0, 0, 0, 0, 0)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"Reference manifest has no records list: {manifest_path}")
    titles = load_bib_titles(project_root / "references/references.bib")
    auxiliary_records = _auxiliary_records(project_root, records)
    index_records = [*records, *auxiliary_records]
    digest = hashlib.sha256()
    digest.update(manifest_path.read_bytes())
    bib_path = project_root / "references/references.bib"
    if bib_path.is_file():
        digest.update(bib_path.read_bytes())

    events: list[dict[str, Any]] = []
    extracted_count = 0
    reused_count = 0
    pdf_count = 0
    pdf_page_count = 0
    sparse_pdf_page_count = 0
    ocr_pdf_page_count = 0
    extraction_error_count = 0
    seen_ids: set[str] = set()
    for record in index_records:
        if not isinstance(record, dict):
            raise ValueError("Reference manifest records must be objects")
        key = str(record.get("key") or record.get("id") or "").strip()
        if not key:
            raise ValueError("Reference manifest record is missing key and id")
        reference_id = f"ref_{_slug(key)}"
        if reference_id in seen_ids:
            raise ValueError(f"Duplicate reference identifier: {reference_id}")
        seen_ids.add(reference_id)

        path_text, recorded_status, expected_sha256, source_url = _artifact_fields(record)
        artifact_path = _resolve_inside(project_root, path_text) if path_text else None
        artifact_exists = bool(artifact_path and artifact_path.is_file())
        actual_sha256 = sha256_file(artifact_path) if artifact_exists and artifact_path else None
        if expected_sha256 and actual_sha256 and expected_sha256 != actual_sha256:
            raise ValueError(
                f"Reference SHA-256 mismatch for {key}: {expected_sha256} != {actual_sha256}"
            )
        ocr_pages, ocr_cache_sha256, ocr_engine = _load_ocr_cache(project_root, actual_sha256)

        title = titles.get(key) or str(record.get("title") or key)
        role = str(record.get("role") or "local research reference")
        kind = str(record.get("kind") or "reference")
        license_note = str(record.get("license_note") or "not recorded")
        status = "available" if artifact_exists else "unavailable"
        extraction_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "extractor_version": EXTRACTOR_VERSION,
                    "record": record,
                    "title": title,
                    "actual_sha256": actual_sha256,
                    "ocr_cache_sha256": ocr_cache_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        output_path = output_root / f"{_slug(key)}.txt"
        marker = f"Index fingerprint: {extraction_fingerprint}"
        cached_prefix = ""
        if output_path.is_file():
            with output_path.open("r", encoding="utf-8", errors="replace") as handle:
                cached_prefix = "".join(handle.readline() for _ in range(16))
        cached = marker in cached_prefix
        extraction_stats: dict[str, Any]
        if cached:
            reused_count += 1
            extraction_stats = _cached_extraction_stats(output_path)
        else:
            body = ""
            extraction_error: str | None = None
            extraction_stats = {"pdf": False}
            if artifact_exists and artifact_path:
                try:
                    body, extraction_stats = _extract_text(artifact_path, ocr_pages, ocr_engine)
                except Exception as exc:  # keep metadata searchable when one PDF is malformed
                    extraction_error = f"{type(exc).__name__}: {exc}"
                    extraction_stats = {
                        "pdf": artifact_path.suffix.lower() == ".pdf",
                        "error": extraction_error,
                    }
            header = [
                f"Reference title: {title}",
                f"Citation key: {key}",
                f"Reference id: {record.get('id', 'not recorded')}",
                f"Kind: {kind}",
                f"Role: {role}",
                f"Source URL: {source_url or 'not recorded'}",
                f"Local artifact: {path_text or 'not recorded'}",
                f"Artifact SHA-256: {actual_sha256 or expected_sha256 or 'not available'}",
                f"Manifest status: {recorded_status or 'not recorded'}",
                f"License note: {license_note}",
                marker,
            ]
            if extraction_stats.get("pdf"):
                header.extend(
                    [
                        f"PDF page count: {extraction_stats.get('page_count', 0)}",
                        "Sparse embedded-text pages: "
                        + json.dumps(extraction_stats.get("sparse_pages", [])),
                        "OCR pages: " + json.dumps(extraction_stats.get("ocr_pages", [])),
                        f"OCR engine: {extraction_stats.get('ocr_engine') or 'not used'}",
                    ]
                )
            if extraction_error:
                header.append(f"Extraction error: {extraction_error}")
            content = "\n".join(header) + "\n\n" + (body or "[No extractable local text]") + "\n"
            _write_if_changed(output_path, content)
            extracted_count += 1

        if extraction_stats.get("pdf"):
            pdf_count += 1
            pdf_page_count += int(extraction_stats.get("page_count", 0))
            sparse_pdf_page_count += len(extraction_stats.get("sparse_pages", []))
            ocr_pdf_page_count += len(extraction_stats.get("ocr_pages", []))
        if extraction_stats.get("error"):
            extraction_error_count += 1

        source_sha256 = sha256_file(output_path)
        relative_output = output_path.relative_to(project_root).as_posix()
        header_end = _header_line_count(output_path)
        summary = (
            f"{title}. Citation key: {key}. Role: {role}. Kind: {kind}. "
            f"Source URL: {source_url or 'not recorded'}. Local artifact: {path_text or 'not recorded'}."
        )
        event = {
            "schema_version": "derived-reference-1.0",
            "event_id": reference_id,
            # References are derived retrieval records, not project events.  The
            # sentinel keeps old clients that lack event-type filtering from
            # treating acquisition time as research recency.
            "date": REFERENCE_SORT_DATE,
            "event_type": REFERENCE_EVENT_TYPE,
            "status": status,
            "project": REFERENCE_PROJECT,
            "workstream": REFERENCE_WORKSTREAM,
            "summary": summary,
            "relations": [
                {"type": "citation_key", "target": key},
                *(
                    [{"type": "source_url", "target": str(source_url)}]
                    if source_url
                    else []
                ),
            ],
            "artifacts": [
                {
                    "path": path_text,
                    "sha256": actual_sha256 or expected_sha256,
                    "role": kind,
                }
            ] if path_text else [],
            "source": {
                "source_path": relative_output,
                "source_sha256": source_sha256,
                "heading": title,
                "line_start": 1,
                "line_end": header_end,
                "legacy_import": False,
                "requires_human_review": False,
            },
            "reference_record": record,
            "reference_extraction": extraction_stats,
        }
        events.append(event)
        digest.update(reference_id.encode("utf-8"))
        digest.update(source_sha256.encode("ascii"))

    return ReferenceCorpus(
        events=events,
        fingerprint=digest.hexdigest(),
        manifest_count=len(records),
        auxiliary_count=len(auxiliary_records),
        extracted_count=extracted_count,
        reused_count=reused_count,
        pdf_count=pdf_count,
        pdf_page_count=pdf_page_count,
        sparse_pdf_page_count=sparse_pdf_page_count,
        ocr_pdf_page_count=ocr_pdf_page_count,
        extraction_error_count=extraction_error_count,
    )
