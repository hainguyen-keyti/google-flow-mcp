"""MCP smoke: start the server in-memory, list tools, call flow_credits for real, check no secret leaks.

uv run python scripts/acceptance/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import re
import sys

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import mcp_server

SECRET = re.compile(r"SAPISID=|__Secure-|Authorization:")


async def main() -> int:
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        low = mcp_server.server._lowlevel_server
        serve = asyncio.create_task(
            low.run(
                server_streams[0],
                server_streams[1],
                low.create_initialization_options(),
                raise_exceptions=True,
            )
        )
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as session:
                await session.initialize()
                names = sorted(t.name for t in (await session.list_tools()).tools)
                print(f"tools/list: {len(names)} tools: {' '.join(names)}")
                result = await session.call_tool("flow_credits", {})
                text = "".join(getattr(c, "text", "") for c in result.content)
                leak = bool(SECRET.search(text))
                print(f"flow_credits: is_error={result.is_error} text={text[:120]} leak={leak}")
                ok = len(names) >= 15 and not result.is_error and '"balance"' in text and not leak
                print("PASS" if ok else "FAIL", "mcp smoke")
                return 0 if ok else 1
        finally:
            serve.cancel()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
