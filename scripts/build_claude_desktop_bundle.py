#!/usr/bin/env python3
"""Pack the Claude Desktop MCPB bundle and fail closed on a stale release pin.

The bundle is a zip container holding ``manifest.json`` at its root. It carries
no package code: it pins the published wheel and launches it through ``uv``, so
a bundle whose pin lags the release silently keeps users on the old version.
That matters for a release that fixes results rather than crashes, because
nothing in the host surfaces the difference.

``@anthropic-ai/mcpb`` remains the reference packer and validator. This script
covers the checks that do not need a Node toolchain; run the reference packer
too when one is available.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_SOURCE = ROOT / "bundles/claude-desktop"
REQUIRED_MANIFEST_KEYS = ("manifest_version", "name", "version", "description", "author", "server")


def bundle_violations(source: Path, release_version: str) -> list[str]:
    """Return contract violations for the unpacked bundle directory."""

    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        return [f"bundle manifest does not exist: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"bundle manifest is not valid JSON: {exc}"]

    violations = [f"manifest is missing {key}" for key in REQUIRED_MANIFEST_KEYS if key not in manifest]
    if manifest.get("version") != release_version:
        violations.append(f"manifest version {manifest.get('version')!r} is not the release {release_version!r}")
    entry_point = manifest.get("server", {}).get("entry_point")
    if not entry_point or not (source / entry_point).is_file():
        violations.append(f"server entry_point is not in the bundle: {entry_point!r}")

    project = source / "pyproject.toml"
    if not project.is_file():
        violations.append(f"bundle pyproject does not exist: {project}")
    elif f"universal-research-mcp=={release_version}" not in project.read_text(encoding="utf-8"):
        violations.append(
            f"bundle does not pin universal-research-mcp=={release_version}; "
            "installing it would keep the previous release"
        )
    return violations


def pack(source: Path, destination: Path) -> Path:
    """Write the bundle deterministically, shallowest path first."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    files = sorted((path for path in source.rglob("*") if path.is_file()), key=lambda path: path.as_posix())
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(source).as_posix())
    with zipfile.ZipFile(destination) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError(f"packed bundle is corrupt: {destination}")
        if "manifest.json" not in bundle.namelist():
            raise RuntimeError("packed bundle has no manifest.json at its root")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=BUNDLE_SOURCE)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    arguments = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from universal_research_mcp import __version__

    violations = bundle_violations(arguments.source, __version__)
    if violations:
        for violation in violations:
            print(f"bundle contract violation: {violation}", file=sys.stderr)
        return 1

    manifest = json.loads((arguments.source / "manifest.json").read_text(encoding="utf-8"))
    destination = arguments.out_dir / f"{manifest['name']}-{__version__}.mcpb"
    pack(arguments.source, destination)
    print(f"packed Claude Desktop bundle: {destination.name} ({destination.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
