from __future__ import annotations

from pathlib import Path

from benchmarks.contracts import read_jsonl
from benchmarks.integrity_fixtures import build_development_fixtures


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "benchmarks/fixtures/integrity-claim-gate-v1/tasks.development.jsonl"


def test_builder_creates_isolated_source_bundles_with_declared_boundaries(tmp_path: Path) -> None:
    manifests = build_development_fixtures(read_jsonl(TASKS), tmp_path / "fixtures")
    assert len(manifests) == 24
    by_state = {str(item["evidence_state"]): item for item in manifests}
    assert by_state["post_index_mutation"]["index_status"]["status"] == "current"
    mutation = by_state["post_index_mutation"]
    reference = next(item for item in mutation["evidence_references"] if item["path"] == "docs/primary.md")
    assert mutation["post_setup_source_sha256"][reference["path"]] != reference["expected_sha256"]
    unregistered = by_state["unregistered_source"]
    assert "docs/unregistered.md" in unregistered["all_paths"]
    assert "docs/unregistered.md" not in unregistered["registered_paths"]
    assert (tmp_path / "fixtures" / "fixture-manifest.json").is_file()
