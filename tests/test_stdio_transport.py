from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import select
import subprocess
import sys
from unittest.mock import AsyncMock

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from mcp.types import LATEST_PROTOCOL_VERSION
import pytest

from universal_research_mcp.secure_harness import posix_stdio
from universal_research_mcp.secure_harness.posix_stdio import (
    DescriptorBackedStdioFastMCP,
)


SERVER_CASES = (
    (
        "memory",
        (
            "from universal_research_mcp.cli import main; "
            "raise SystemExit(main(['serve', '--no-auto-index']))"
        ),
        "Universal Research",
        "memory_search_candidates",
    ),
    (
        "governance",
        (
            "from universal_research_mcp.governance_server import main; "
            "raise SystemExit(main())"
        ),
        "Universal Research Governance",
        "governance_get_capabilities",
    ),
    (
        "agent-runtime",
        (
            "from universal_research_mcp.runtime_server import main; "
            "raise SystemExit(main([]))"
        ),
        "Universal Research Agent Runtime",
        "agent_runtime_preflight",
    ),
)


def _request(process: subprocess.Popen[str], message: dict[str, object]) -> dict:
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    readable, _, _ = select.select([process.stdout], [], [], 10)
    if not readable:
        return_code = process.poll()
        stderr = process.stderr.read() if return_code is not None else ""
        pytest.fail(
            "stdio MCP did not respond within ten seconds "
            f"(return_code={return_code}, stderr={stderr!r})"
        )
    line = process.stdout.readline()
    assert line, "stdio MCP closed stdout before returning a response"
    return json.loads(line)


def _stop(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _environment(source_root: Path, project_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(source_root),
        "PYTHONSAFEPATH": "1",
        "UNIVERSAL_RESEARCH_ROOT": str(project_root),
    })
    return environment


async def _sdk_round_trip(
    launcher: str,
    source_root: Path,
    foreign_cwd: Path,
) -> tuple[str, set[str]]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-c", launcher],
        cwd=str(foreign_cwd),
        env=_environment(source_root, foreign_cwd),
    )
    with anyio.fail_after(10):
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                return (
                    initialized.serverInfo.name,
                    {tool.name for tool in listed.tools},
                )


@pytest.mark.skipif(os.name != "posix", reason="descriptor stdio requires POSIX")
@pytest.mark.parametrize(
    ("case_id", "launcher", "server_name", "expected_tool"), SERVER_CASES,
)
def test_bundled_stdio_servers_complete_real_mcp_round_trip(
    tmp_path: Path,
    case_id: str,
    launcher: str,
    server_name: str,
    expected_tool: str,
) -> None:
    source_root = Path(__file__).resolve().parents[1]
    foreign_cwd = tmp_path / case_id
    foreign_cwd.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-c", launcher],
        cwd=foreign_cwd,
        env=_environment(source_root, foreign_cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        initialized = _request(process, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "urmcp-stdio-test", "version": "1.0"},
            },
        })
        assert initialized["id"] == 1
        assert initialized["result"]["serverInfo"]["name"] == server_name
        assert process.stdin is not None
        process.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }) + "\n")
        process.stdin.flush()

        listed = _request(process, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        assert listed["id"] == 2
        assert expected_tool in {
            tool["name"] for tool in listed["result"]["tools"]
        }
    finally:
        _stop(process)


@pytest.mark.skipif(os.name != "posix", reason="descriptor stdio requires POSIX")
@pytest.mark.parametrize(
    ("case_id", "launcher", "server_name", "expected_tool"), SERVER_CASES,
)
def test_bundled_stdio_servers_work_with_official_mcp_client(
    tmp_path: Path,
    case_id: str,
    launcher: str,
    server_name: str,
    expected_tool: str,
) -> None:
    source_root = Path(__file__).resolve().parents[1]
    foreign_cwd = tmp_path / case_id
    foreign_cwd.mkdir()

    actual_name, tools = anyio.run(
        _sdk_round_trip, launcher, source_root, foreign_cwd,
    )

    assert actual_name == server_name
    assert expected_tool in tools


def test_non_posix_stdio_uses_fastmcp_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    server = DescriptorBackedStdioFastMCP("fallback-test")
    fallback = AsyncMock()
    monkeypatch.setattr(posix_stdio.os, "name", "nt")
    monkeypatch.setattr(FastMCP, "run_stdio_async", fallback)

    asyncio.run(server.run_stdio_async())

    fallback.assert_awaited_once_with()
