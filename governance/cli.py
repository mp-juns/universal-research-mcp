"""Read-only URAG command line interface for local contract diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from governance.registry import registry_report
from governance.validation import validate_decision, validate_task_packet
from integrations.codex.adapter import (
    build_critical_review_batch,
    build_dispatch_request,
    build_scope_governor_receipt,
    capture_decision,
    serialize_dispatch_manifest,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="urgov", description="Read-only Universal Research Agent Governance diagnostics.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    registry_parser = subparsers.add_parser("registry")
    registry_parser.add_argument("action", choices=["validate"])
    packet_parser = subparsers.add_parser("packet")
    packet_parser.add_argument("action", choices=["validate"])
    packet_parser.add_argument("packet", type=Path)
    decision_parser = subparsers.add_parser("decision")
    decision_parser.add_argument("action", choices=["validate"])
    decision_parser.add_argument("decision", type=Path)
    decision_parser.add_argument("--packet", required=True, type=Path)
    dispatch_parser = subparsers.add_parser("dispatch")
    dispatch_parser.add_argument("action", choices=["receipt", "prepare", "critical-batch", "capture"])
    dispatch_parser.add_argument("packet", nargs="*", type=Path)
    dispatch_parser.add_argument("--decision", type=Path)
    dispatch_parser.add_argument("--governor-receipt", type=Path)
    args = parser.parse_args(argv)
    if args.command == "registry":
        report = registry_report()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if not report["issues"] else 2
    if args.command == "packet":
        issues = validate_task_packet(_read_json(args.packet))
    elif args.command == "decision":
        packet = _read_json(args.packet)
        issues = validate_decision(_read_json(args.decision), packet)
    else:
        packets = [_read_json(path) for path in args.packet]
        receipt = _read_json(args.governor_receipt) if args.governor_receipt else None
        if args.action == "receipt":
            if len(packets) < 2 or args.decision is None:
                parser.error("dispatch receipt requires a governor packet, governed packet(s), and --decision")
            captured = capture_decision(packets[0], _read_json(args.decision))
            result = build_scope_governor_receipt(packets[0], captured, packets[1:])
            print(json.dumps(
                result["receipt"] if result.get("valid") else result,
                ensure_ascii=False,
                sort_keys=True,
            ))
            return 0 if result.get("valid") else 2
        if args.action == "prepare":
            if len(packets) != 1:
                parser.error("dispatch prepare requires exactly one packet")
            dispatch = build_dispatch_request(packets[0], receipt)
            print(
                serialize_dispatch_manifest(
                    dispatch,
                    expected_manifest_hash=str(dispatch.get("dispatch_hash") or ""),
                )
                if dispatch.get("dispatchable")
                else json.dumps(dispatch, ensure_ascii=False, sort_keys=True)
            )
            return 0 if dispatch.get("dispatchable") else 2
        if args.action == "critical-batch":
            dispatch = build_critical_review_batch(packets, receipt)
            print(
                serialize_dispatch_manifest(
                    dispatch,
                    expected_manifest_hash=str(dispatch.get("batch_hash") or ""),
                )
                if dispatch.get("dispatchable")
                else json.dumps(dispatch, ensure_ascii=False, sort_keys=True)
            )
            return 0 if dispatch.get("dispatchable") else 2
        if len(packets) != 1 or args.decision is None:
            parser.error("dispatch capture requires one packet and --decision")
        captured = capture_decision(packets[0], _read_json(args.decision))
        print(json.dumps(captured, ensure_ascii=False, sort_keys=True))
        return 0 if captured.get("accepted") else 2
    print(json.dumps({"valid": not issues, "issues": issues}, ensure_ascii=False, sort_keys=True))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
