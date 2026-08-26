"""POSIX descriptor-backed MCP stdio transport for thread-constrained hosts."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.fastmcp import FastMCP
import mcp.types as types
from mcp.shared.message import SessionMessage


_MAX_MESSAGE_BYTES = 16 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class DescriptorBackedStdioFastMCP(FastMCP):
    """Use event-loop descriptor I/O where executor-thread stdin can stall."""

    async def run_stdio_async(self) -> None:
        if os.name != "posix":  # pragma: no cover - exercised by fallback unit test
            await super().run_stdio_async()
            return
        async with posix_stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self._mcp_server.create_initialization_options(),
            )


async def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = payload
    while remaining:
        await anyio.wait_writable(descriptor)
        written = os.write(descriptor, remaining)
        if written <= 0:  # pragma: no cover - defensive OS boundary
            raise BrokenPipeError("MCP stdio descriptor stopped accepting bytes")
        remaining = remaining[written:]


@asynccontextmanager
async def posix_stdio_server(stdin_descriptor: int = 0, stdout_descriptor: int = 1):
    """Expose newline-delimited MCP streams without executor-thread file reads."""
    if os.name != "posix":  # pragma: no cover - caller falls back before this path
        raise RuntimeError("descriptor-backed MCP stdio requires POSIX")

    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def send_line(raw: bytes) -> None:
        try:
            message = types.JSONRPCMessage.model_validate_json(
                raw.decode("utf-8", errors="replace")
            )
        except Exception as exc:
            await read_stream_writer.send(exc)
            return
        await read_stream_writer.send(SessionMessage(message))

    async def stdin_reader() -> None:
        buffer = bytearray()
        discarding_oversized_line = False
        try:
            async with read_stream_writer:
                while True:
                    await anyio.wait_readable(stdin_descriptor)
                    chunk = os.read(stdin_descriptor, _READ_CHUNK_BYTES)
                    if not chunk:
                        if buffer and not discarding_oversized_line:
                            await send_line(bytes(buffer))
                        break
                    buffer.extend(chunk)
                    while True:
                        newline = buffer.find(b"\n")
                        if newline < 0:
                            if len(buffer) > _MAX_MESSAGE_BYTES:
                                await read_stream_writer.send(
                                    ValueError("MCP stdio message exceeds the byte ceiling")
                                )
                                buffer.clear()
                                discarding_oversized_line = True
                            break
                        raw = bytes(buffer[:newline])
                        del buffer[:newline + 1]
                        if discarding_oversized_line:
                            discarding_oversized_line = False
                            continue
                        if len(raw) > _MAX_MESSAGE_BYTES:
                            await read_stream_writer.send(
                                ValueError("MCP stdio message exceeds the byte ceiling")
                            )
                            continue
                        await send_line(raw)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            await anyio.lowlevel.checkpoint()

    async def stdout_writer() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    payload = (
                        session_message.message.model_dump_json(
                            by_alias=True,
                            exclude_none=True,
                        )
                        + "\n"
                    ).encode("utf-8")
                    await _write_all(stdout_descriptor, payload)
        except (
            BrokenPipeError,
            anyio.BrokenResourceError,
            anyio.ClosedResourceError,
        ):
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdin_reader)
        task_group.start_soon(stdout_writer)
        try:
            yield read_stream, write_stream
        finally:
            task_group.cancel_scope.cancel()
