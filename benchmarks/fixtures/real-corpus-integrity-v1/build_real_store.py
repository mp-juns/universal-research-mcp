#!/usr/bin/env python3
"""Build the real-corpus benchmark store from a reference research project.

Read-only over the reference project. Curation rule (preregistered):
1. Collect every event source reference; keep only references that carry a
   SHA-256 and whose exact revision exists in the reference project's git
   history or worktree.
2. Choose one revision per path — the revision referenced by the most
   otherwise-keepable events (ties: lexicographic smallest hash).
3. Keep every event whose references all match the chosen revisions; drop the
   rest. Rewrite sources.jsonl to exactly the chosen (path, revision) set.
4. Materialize chosen revisions, build the lexical index, verify `current`.
5. Apply reality (--apply-drift): overwrite every chosen path whose current
   worktree content differs with that current content. These are the natural
   post-index mutations; a manifest records registered/current hashes.

Dropped events and sha-less references are the natural broken-chain
condition; nothing is injected or edited by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_revisions(reference: Path, path: str) -> list[str]:
    out = subprocess.run(["git", "rev-list", "--all", "--", path],
                         capture_output=True, text=True, cwd=reference)
    return out.stdout.split()


def blob_at(reference: Path, path: str, want: str) -> bytes | None:
    current = reference / path
    if current.is_file():
        data = current.read_bytes()
        if sha256(data) == want:
            return data
    for rev in git_revisions(reference, path):
        data = subprocess.run(["git", "show", f"{rev}:{path}"],
                              capture_output=True, cwd=reference).stdout
        if sha256(data) == want:
            return data
    return None


def event_refs(event: dict) -> list[tuple[str, str | None]]:
    sources = [event.get("source")] if isinstance(event.get("source"), dict) else []
    sources += [s for s in (event.get("sources") or []) if isinstance(s, dict)]
    refs = []
    for s in sources:
        path = s.get("source_path") or s.get("path")
        digest = (s.get("source_sha256") or s.get("sha256") or "").lower() or None
        if path:
            refs.append((path, digest))
    return refs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True,
                        help="Reference research project root (read-only)")
    parser.add_argument("--events", type=Path, default=Path("research-events"),
                        help="Ledger directory relative to the reference root")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply-drift", action="store_true")
    args = parser.parse_args()
    ref = args.reference.resolve()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing non-empty output: {out}")
    events_root = ref / args.events

    events = []
    for f in sorted((events_root / "daily").glob("*/events.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append((f.parent.name, json.loads(line)))

    resolvable: dict[tuple[str, str], bool] = {}
    def can_resolve(path: str, digest: str) -> bool:
        key = (path, digest)
        if key not in resolvable:
            resolvable[key] = blob_at(ref, path, digest) is not None
        return resolvable[key]

    candidates = []
    for day, event in events:
        refs = event_refs(event)
        ok = all(digest and can_resolve(path, digest) for path, digest in refs)
        candidates.append((day, event, refs, ok and bool(refs)))

    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _day, _event, refs, ok in candidates:
        if ok:
            for path, digest in set(refs):
                votes[path][digest] += 1
    choice = {p: max(sorted(hs), key=lambda h: hs[h]) for p, hs in votes.items()}

    kept, dropped = [], []
    for day, event, refs, ok in candidates:
        if ok and all(choice[p] == h for p, h in refs):
            kept.append((day, event))
        else:
            dropped.append(event["event_id"])

    (out / "data/events").mkdir(parents=True)
    for path, digest in sorted(choice.items()):
        data = blob_at(ref, path, digest)
        assert data is not None
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with (out / "data/events/sources.jsonl").open("w", encoding="utf-8") as h:
        for index, (path, digest) in enumerate(sorted(choice.items()), 1):
            h.write(json.dumps({
                "source_id": f"src_curated_{index:04d}", "source_path": path,
                "source_sha256": digest,
                "source_type": "markdown" if path.endswith(".md") else "file",
                "legacy_import": True}, ensure_ascii=False, sort_keys=True) + "\n")
    for day, event in kept:
        target = out / "data/events/daily" / day / "events.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as h:
            h.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from universal_research_mcp.indexing import ensure_lexical_index
    report = ensure_lexical_index(out)
    if report.get("status") != "current":
        raise SystemExit(f"index not current: {report.get('status')}")

    drift = []
    for path, digest in sorted(choice.items()):
        current = ref / path
        if not current.is_file():
            continue
        data = current.read_bytes()
        if sha256(data) != digest:
            drift.append({"source_path": path, "registered_sha256": digest,
                          "current_sha256": sha256(data)})
            if args.apply_drift:
                (out / path).write_bytes(data)
    manifest = {
        "schema_version": "real-corpus-store/1.0",
        "reference_root_sha256": sha256(str(ref).encode()),
        "event_total": len(events), "event_kept": len(kept),
        "event_dropped": len(dropped), "registered_paths": len(choice),
        "natural_drift": drift, "drift_applied": bool(args.apply_drift),
        "index": {k: report.get(k) for k in ("status", "indexed_fingerprint")},
    }
    (out / "store-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in
                      ("event_kept", "event_dropped", "registered_paths",
                       "drift_applied")} |
                     {"drift_paths": len(drift)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
