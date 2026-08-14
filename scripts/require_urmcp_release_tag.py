#!/usr/bin/env python3
"""Require an ``urmcp-v*`` release tag to match alias package metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "packages" / "urmcp" / "pyproject.toml"


def expected_tag() -> str:
    """Return the only release tag permitted for the checked-out alias wheel."""

    with PYPROJECT.open("rb") as handle:
        return f"urmcp-v{tomllib.load(handle)['project']['version']}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    args = parser.parse_args()
    expected = expected_tag()
    if args.tag != expected:
        parser.error(f"release tag must be {expected}, received {args.tag}")
    print(f"validated urmcp release tag: {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
