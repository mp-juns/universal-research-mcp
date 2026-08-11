"""Fresh init must not claim an unconfigured semantic index is ready."""

from __future__ import annotations

import json
from pathlib import Path

from universal_research_mcp import cli


def test_init_creates_lexical_only_until_semantic_is_explicitly_configured(
    tmp_path: Path, capsys
) -> None:
    assert cli.main(["init", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["lexical"]["status"] == "current"
    assert report["semantic"]["status"] == "missing"
    assert not (tmp_path / "data/index/semantic.sqlite").exists()
