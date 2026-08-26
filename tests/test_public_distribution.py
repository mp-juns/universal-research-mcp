import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile

from universal_research_mcp.governance.registry import FIXED_ROSTER
from universal_research_mcp import __version__
from universal_research_mcp.session_scope import SESSION_SCOPE_INSTRUCTIONS
from universal_research_mcp.tools.distribution import (
    BUNDLE_PREFIX,
    GOVERNANCE_ROLE_IDS,
    REQUIRED_BUNDLE_FILES,
    REQUIRED_GOVERNANCE_FILES,
    REQUIRED_RUNTIME_FILES,
    validate_wheel,
)


ROOT = Path(__file__).resolve().parents[1]


class PublicDistributionTests(unittest.TestCase):
    def test_package_exposes_stable_mcp_entry_point(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        self.assertEqual(project["project"]["name"], "universal-research-mcp")
        self.assertEqual(project["project"]["version"], __version__)
        self.assertEqual(project["project"]["license"], "MIT")
        self.assertEqual(project["project"]["license-files"], ["LICENSE"])
        self.assertEqual(project["project"]["authors"], [{"name": "mp-juns"}])
        self.assertEqual(
            project["project"]["scripts"]["universal-research-mcp"],
            "universal_research_mcp.cli:legacy_main",
        )
        self.assertEqual(
            project["project"]["scripts"]["universal-research"],
            "universal_research_mcp.cli:main",
        )
        self.assertEqual(
            project["project"]["scripts"]["urmcp"],
            "universal_research_mcp.cli:main",
        )
        self.assertNotIn(
            "universal-research-agent-runtime",
            project["project"]["scripts"],
        )
        self.assertFalse(
            any(
                dependency.lower().split("[")[0].startswith("websockets")
                for dependency in project["project"]["dependencies"]
            )
        )
        self.assertIn("MIT License", (ROOT / "LICENSE").read_text(encoding="utf-8"))

    def test_distribution_declares_schema_pack_and_plugin_bundle(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        data_files = project["tool"]["setuptools"]["data-files"]
        package_data = project["tool"]["setuptools"]["package-data"]["universal_research_mcp.governance"]
        self.assertIn("share/universal-research-mcp/docs", data_files)
        self.assertIn("share/universal-research-mcp/schemas", data_files)
        self.assertIn("share/universal-research-mcp/packs/study_type", data_files)
        self.assertIn(
            "share/universal-research-mcp/plugin/universal-research-memory",
            data_files,
        )
        self.assertIn("roles/*/role.yaml", package_data)
        self.assertIn("roles/*/instructions.md", package_data)
        self.assertIn("schemas/*.json", package_data)
        self.assertEqual(
            set(data_files["share/universal-research-mcp/docs"]),
            {
                "docs/architecture.md",
                "docs/failure-policy.md",
                "docs/host-integration.md",
                "docs/public-demo.md",
                "docs/research-operations-specification.md",
                "docs/secure-harness.md",
                "docs/semantic-retrieval.md",
                "docs/security.md",
            },
        )
        self.assertEqual(
            data_files["share/universal-research-mcp/docs/decisions"],
            ["docs/decisions/*.md"],
        )

    def test_wheel_validator_requires_every_role_prompt_pack_member(self) -> None:
        self.assertEqual(set(GOVERNANCE_ROLE_IDS), FIXED_ROSTER)
        self.assertIn("universal_research_mcp/core/ingest.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/runtime/ingest_approval.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/integrations/codex/adapter.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/governance/agent_creation.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/runtime/agent_approval.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/integrations/codex/agent_control.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/secure_harness/approval.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/secure_harness/contracts.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/secure_harness/codex_runner.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("universal_research_mcp/secure_harness/worker.py", REQUIRED_RUNTIME_FILES)
        self.assertNotIn("universal_research_mcp/runtime_server.py", REQUIRED_RUNTIME_FILES)
        self.assertIn("docs/host-integration.md", REQUIRED_BUNDLE_FILES)
        self.assertNotIn("docs/agent-runtime.md", REQUIRED_BUNDLE_FILES)
        self.assertEqual(
            {
                path
                for path in REQUIRED_GOVERNANCE_FILES
                if path.endswith("/role.yaml")
            },
            {
                f"universal_research_mcp/governance/roles/{agent_id}/role.yaml"
                for agent_id in FIXED_ROSTER
            },
        )
        self.assertEqual(
            {
                path
                for path in REQUIRED_GOVERNANCE_FILES
                if path.endswith("/instructions.md")
            },
            {
                f"universal_research_mcp/governance/roles/{agent_id}/instructions.md"
                for agent_id in FIXED_ROSTER
            },
        )
        self.assertIn(
            "universal_research_mcp/governance/schemas/prompt-pack.schema.json",
            REQUIRED_GOVERNANCE_FILES,
        )

        omitted = "universal_research_mcp/governance/roles/retrieval_governor/instructions.md"
        wheel_members = {
            *REQUIRED_RUNTIME_FILES,
            *REQUIRED_GOVERNANCE_FILES,
            *(BUNDLE_PREFIX + name for name in REQUIRED_BUNDLE_FILES),
            f"universal_research_mcp-{__version__}.dist-info/entry_points.txt",
        }
        wheel_members.remove(omitted)
        with tempfile.TemporaryDirectory() as temporary_directory:
            wheel = Path(temporary_directory) / "fixture.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for member in sorted(wheel_members):
                    archive.writestr(member, "")
            self.assertEqual(validate_wheel(wheel), [omitted])

    def test_plugin_has_no_repository_relative_launcher_path(self) -> None:
        config = json.loads(
            (ROOT / "plugin/universal-research-memory/.mcp.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            list(config["mcpServers"]),
            ["universal_research"],
        )
        server = config["mcpServers"]["universal_research"]
        self.assertEqual(server["command"], "universal-research")
        self.assertEqual(server["args"], ["serve", "--no-auto-index"])
        self.assertNotIn("env", server)
        manifest = json.loads(
            (
                ROOT / "plugin/universal-research-memory/.codex-plugin/plugin.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(manifest["version"].startswith(f"{__version__}+codex."))
        self.assertEqual(manifest["author"]["name"], "mp-juns")
        self.assertEqual(manifest["interface"]["developerName"], "mp-juns")
        self.assertIn("Codex-only", manifest["description"])
        self.assertIn("Codex host", manifest["interface"]["longDescription"])
        self.assertIn("external model APIs", manifest["interface"]["longDescription"])
        self.assertIn("Codex host-owned", manifest["interface"]["defaultPrompt"])
        self.assertIn("visualization off", manifest["interface"]["defaultPrompt"])

    def test_session_hook_emits_scope_without_reading_transcripts_or_writing_state(self) -> None:
        plugin = ROOT / "plugin/universal-research-memory"
        policy = (plugin / "hooks/session-scope.md").read_text(encoding="utf-8").strip()
        self.assertEqual(policy, SESSION_SCOPE_INSTRUCTIONS)
        hook = plugin / "scripts/session_start.py"
        for source in ("startup", "clear", "resume", "compact", {"untrusted": True}):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                payload = {
                    "hook_event_name": "SessionStart", "source": source,
                    "session_id": "private-marker", "transcript_path": "/not-readable/private-marker",
                    "cwd": "/not-readable/private-marker",
                }
                result = subprocess.run(
                    [sys.executable, "-I", str(hook)], input=json.dumps(payload),
                    text=True, capture_output=True, check=True, cwd=root, timeout=10,
                )
                output = json.loads(result.stdout)
                context = output["hookSpecificOutput"]["additionalContext"]
                self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
                self.assertIn(policy, context)
                self.assertNotIn("private-marker", result.stdout + result.stderr)
                self.assertEqual(list(root.iterdir()), [])
                if isinstance(source, str) and source in {"resume", "compact"}:
                    self.assertTrue(context.startswith("Same-session continuation:"))
                else:
                    self.assertTrue(context.startswith("Fresh or unverified session boundary:"))

    def test_session_hook_never_turns_invalid_event_metadata_into_approval(self) -> None:
        hook = ROOT / "plugin/universal-research-memory/scripts/session_start.py"
        for payload in ("not-json", "null", "[]", "x" * 65_537):
            with self.subTest(payload=payload[:20]):
                result = subprocess.run(
                    [sys.executable, "-I", str(hook)], input=payload, text=True,
                    capture_output=True, check=True, timeout=10,
                )
                output = json.loads(result.stdout)
                self.assertEqual(set(output), {"hookSpecificOutput"})
                self.assertTrue(output["hookSpecificOutput"]["additionalContext"].startswith(
                    "Fresh or unverified session boundary:",
                ))

        unrelated = subprocess.run(
            [sys.executable, "-I", str(hook)],
            input=json.dumps({"hook_event_name": "PreToolUse"}), text=True,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(json.loads(unrelated.stdout), {})


if __name__ == "__main__":
    unittest.main()
