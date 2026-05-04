"""Entry point: `python -m openclaw_output_vetter_mcp` runs a stdio MCP server."""
from __future__ import annotations

import asyncio
import os

from mcp.server.stdio import stdio_server

from openclaw_output_vetter_mcp.server import build_server


def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    backend_name = os.environ.get("OPENCLAW_VETTER_BACKEND", "default")
    server = build_server(backend_name=backend_name)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    main()
