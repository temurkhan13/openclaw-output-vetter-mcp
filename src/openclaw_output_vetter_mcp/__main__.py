"""Entry point: `python -m openclaw_output_vetter_mcp` runs a stdio MCP server."""
from __future__ import annotations

import asyncio
import os
import sys

from mcp.server.stdio import stdio_server

from openclaw_output_vetter_mcp import __version__
from openclaw_output_vetter_mcp.server import build_server


def _emit_startup_banner(backend_name: str) -> None:
    """Print a one-line value-prove banner to stderr at startup.

    Goes to stderr (stdout is reserved for MCP JSON-RPC protocol traffic).
    Suppressible via `OPENCLAW_VETTER_QUIET=1` for users who pipe stderr to a log file.
    """
    if os.environ.get("OPENCLAW_VETTER_QUIET", "").strip() in {"1", "true", "yes"}:
        return
    banner = (
        f"openclaw-output-vetter-mcp v{__version__} ready · "
        f"post-action verify (response-grounding + claim-vs-action divergence + entity-mismatch) · "
        f"backend={backend_name}"
    )
    print(banner, file=sys.stderr, flush=True)


def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    backend_name = os.environ.get("OPENCLAW_VETTER_BACKEND", "default")
    _emit_startup_banner(backend_name)
    server = build_server(backend_name=backend_name)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    main()
