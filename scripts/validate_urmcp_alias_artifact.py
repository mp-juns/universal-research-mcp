#!/usr/bin/env python3
"""Fail closed if the standalone ``urmcp`` wheel stops being a thin alias."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from pathlib import Path
import zipfile


def validate_wheel(path: Path) -> list[str]:
    """Return contract violations for a built ``urmcp`` alias wheel."""

    if not path.is_file():
        return [f"wheel does not exist: {path}"]
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")),
            None,
        )
        entry_points_name = next(
            (name for name in names if name.endswith(".dist-info/entry_points.txt")),
            None,
        )
        if metadata_name is None or entry_points_name is None:
            return ["wheel lacks distribution metadata or entry points"]
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        entry_points = archive.read(entry_points_name).decode("utf-8")
    problems: list[str] = []
    if metadata.get("Name") != "urmcp":
        problems.append("distribution name is not urmcp")
    version = metadata.get("Version")
    if not version:
        problems.append("distribution version is missing")
    elif f"universal-research-mcp=={version}" not in {
        requirement.replace(" ", "")
        for requirement in metadata.get_all("Requires-Dist", [])
    }:
        problems.append("core dependency is not pinned to the alias version")
    if "urmcp = universal_research_mcp.cli:main" not in entry_points:
        problems.append("urmcp does not delegate to the core CLI")
    forbidden = tuple(name for name in names if name.startswith("universal_research_mcp/"))
    if forbidden:
        problems.append("alias wheel contains a duplicate core MCP implementation")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    problems = validate_wheel(args.wheel)
    if problems:
        for problem in problems:
            print(f"invalid urmcp alias wheel: {problem}")
        return 1
    print(f"validated urmcp alias wheel: {args.wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
