"""Emit stateless SessionStart context; never read transcripts or grant scope."""
from __future__ import annotations

import json
from pathlib import Path
import sys

MAX_EVENT_BYTES = 65_536


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
    try:
        event = json.loads(raw) if len(raw) <= MAX_EVENT_BYTES else None
    except (ValueError, UnicodeError):
        event = None
    source = event.get("source") if isinstance(event, dict) else None
    if isinstance(event, dict) and event.get("hook_event_name") not in (None, "SessionStart"):
        print("{}")
        return 0
    policy = (Path(__file__).resolve().parents[1] / "hooks/session-scope.md").read_text(encoding="utf-8").strip()
    if isinstance(source, str) and source in {"resume", "compact"}:
        context = (
            "Same-session continuation: preserve only an explicit user-confirmed "
            "scope in this session; otherwise ask before execution.\n\n" + policy
        )
    else:
        context = (
            "Fresh or unverified session boundary: ask the session-scope question "
            "as your first response and WAIT for the user's reply.\n\n" + policy
        )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
