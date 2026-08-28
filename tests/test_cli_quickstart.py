from __future__ import annotations

import json
from pathlib import Path

from universal_research_mcp import cli
from universal_research_mcp.indexing import index_status


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "qs"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "calibration.md").write_text(
        "# Calibration\n\nThe dead-time correction is 4.62 microseconds.\n", encoding="utf-8",
    )
    (root / "notes.md").write_text(
        "# Lab notes\n\nCryostat base temperature 18.4 millikelvin.\n", encoding="utf-8",
    )
    return root


def test_quickstart_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path)
    assert cli.main(["quickstart", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run"
    assert sorted(report["would_register"]) == ["docs/calibration.md", "notes.md"]
    assert not (root / "data").exists()


def test_quickstart_builds_store_and_is_idempotent(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path)
    assert cli.main(["quickstart", str(root), "--yes"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ready"
    assert report["sources_registered"] == 2
    assert report["records_appended"] == 2
    assert index_status(root)["status"] == "current"
    registry = [
        json.loads(line)
        for line in (root / "data/events/sources.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {entry["source_path"] for entry in registry} == {"docs/calibration.md", "notes.md"}
    # every observation carries a whole-file hash-bound locator and an approval ref
    events = []
    for day in sorted((root / "data/events/daily").glob("*/events.jsonl")):
        events += [json.loads(line) for line in day.read_text(encoding="utf-8").splitlines() if line.strip()]
    observations = [event for event in events if event["record_kind"] == "observation"]
    assert len(observations) == 2
    for event in observations:
        assert event["approval_refs"] == ["approval_study_quickstart"]
        locator = event["source_refs"][0]["locator"]
        assert locator["start"] == 1 and locator["end"] >= 3

    assert cli.main(["quickstart", str(root), "--yes"]) == 0
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["sources_registered"] == 0
    assert rerun["sources_already_current"] == 2
    assert rerun["records_appended"] == 0


def test_quickstart_reports_empty_folder(tmp_path: Path, capsys) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    assert cli.main(["quickstart", str(root), "--yes"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "nothing_to_register"
