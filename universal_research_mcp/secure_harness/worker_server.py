"""Opt-in stdio MCP exposing a single approved Docker worker session."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Sequence

from mcp.server.fastmcp import FastMCP

from .approval import HarnessApprovalStore
from .posix_stdio import posix_stdio_server
from .worker import WorkerSession


class SecureWorkerFastMCP(FastMCP):
    """Use event-loop descriptor I/O where executor-thread stdin can stall."""

    async def run_stdio_async(self) -> None:
        if os.name != "posix":  # pragma: no cover - exercised on supported host families
            await super().run_stdio_async()
            return
        async with posix_stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self._mcp_server.create_initialization_options(),
            )


mcp = SecureWorkerFastMCP(
    "Universal Research Secure Worker",
    instructions=(
        "Only call operations already sealed in the current run plan. This server "
        "cannot expand scope, grant approval, enable network, or alter the host project."
    ),
)
_SESSION: WorkerSession | None = None


def configure_session(session: WorkerSession) -> None:
    global _SESSION
    _SESSION = session


def _session() -> WorkerSession:
    if _SESSION is None:
        raise RuntimeError("secure worker session is not configured")
    return _SESSION


@mcp.tool()
def worker_read(operation_id: str, path: str, start_line: int, end_line: int) -> dict[str, Any]:
    return _session().read(operation_id, path, start_line, end_line)


@mcp.tool()
def worker_search(operation_id: str, query: str, limit: int = 20) -> dict[str, Any]:
    return _session().search(operation_id, query, limit)


@mcp.tool()
def worker_write(operation_id: str, path: str, expected_sha256: str, content: str) -> dict[str, Any]:
    return _session().write(operation_id, path, expected_sha256, content)


@mcp.tool()
def worker_execute(operation_id: str) -> dict[str, Any]:
    return _session().execute(operation_id)


@mcp.tool()
def worker_inventory() -> dict[str, Any]:
    return _session().inventory()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="universal-research-secure-worker")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--approval-state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    configure_session(WorkerSession(
        project_root=args.root,
        plan_path=args.plan,
        manifest_path=args.manifest,
        workspace=args.workspace,
        approval_store=HarnessApprovalStore(args.root, state_root=args.approval_state_root),
    ))
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
