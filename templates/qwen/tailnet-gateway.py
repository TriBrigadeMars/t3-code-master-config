#!/usr/bin/env python3
"""Expose the desktop Qwen router only to the configured Tailscale peers.

Environment:
  QWEN_GATEWAY_LISTEN_HOST   Tailscale IP to bind (required)
  QWEN_GATEWAY_LISTEN_PORT   default 1235
  QWEN_GATEWAY_UPSTREAM_HOST default 127.0.0.1
  QWEN_GATEWAY_UPSTREAM_PORT default 1234
  QWEN_GATEWAY_ALLOWED_PEERS comma-separated peer IPs (required)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys

LISTEN_HOST = os.environ.get("QWEN_GATEWAY_LISTEN_HOST", "").strip()
LISTEN_PORT = int(os.environ.get("QWEN_GATEWAY_LISTEN_PORT", "1235"))
UPSTREAM_HOST = os.environ.get("QWEN_GATEWAY_UPSTREAM_HOST", "127.0.0.1").strip()
UPSTREAM_PORT = int(os.environ.get("QWEN_GATEWAY_UPSTREAM_PORT", "1234"))
ALLOWED_PEERS = {
    peer.strip()
    for peer in os.environ.get("QWEN_GATEWAY_ALLOWED_PEERS", "").split(",")
    if peer.strip()
}


async def copy_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


def bad_gateway(writer: asyncio.StreamWriter, message: str) -> None:
    body = json.dumps({"error": message}).encode("utf-8")
    writer.write(
        b"HTTP/1.1 502 Bad Gateway\r\n"
        b"Content-Type: application/json\r\n"
        b"Connection: close\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
    )


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    if not peer or peer[0] not in ALLOWED_PEERS:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return

    upstream = None
    try:
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        upstream_reader, upstream = await asyncio.wait_for(
            asyncio.open_connection(UPSTREAM_HOST, UPSTREAM_PORT), timeout=5
        )
        upstream.write(header)
        await upstream.drain()
        await asyncio.gather(
            copy_stream(reader, upstream),
            copy_stream(upstream_reader, writer),
        )
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            ConnectionError, OSError):
        with contextlib.suppress(Exception):
            bad_gateway(writer, "upstream unavailable")
            await writer.drain()
    finally:
        if upstream is not None:
            upstream.close()
            with contextlib.suppress(Exception):
                await upstream.wait_closed()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def preflight() -> None:
    try:
        _, upstream = await asyncio.wait_for(
            asyncio.open_connection(UPSTREAM_HOST, UPSTREAM_PORT), timeout=5
        )
        upstream.close()
        with contextlib.suppress(Exception):
            await upstream.wait_closed()
    except (asyncio.TimeoutError, ConnectionError, OSError) as error:
        raise SystemExit(
            f"upstream {UPSTREAM_HOST}:{UPSTREAM_PORT} is unavailable: {error}"
        ) from error


async def main() -> None:
    if not LISTEN_HOST:
        raise SystemExit("QWEN_GATEWAY_LISTEN_HOST is required")
    if not ALLOWED_PEERS:
        raise SystemExit("QWEN_GATEWAY_ALLOWED_PEERS is required")
    await preflight()
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT, limit=65536)
    print(
        f"qwen gateway listening on {LISTEN_HOST}:{LISTEN_PORT} -> "
        f"{UPSTREAM_HOST}:{UPSTREAM_PORT}; peers={sorted(ALLOWED_PEERS)}",
        file=sys.stderr,
        flush=True,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
