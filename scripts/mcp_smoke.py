#!/usr/bin/env python3
"""Smoke check for `oknoll mcp stdio`: spawn the server and drive it over the
real stdio JSON-RPC transport (no model). Verifies the seven tools are present
and one bounded call round-trips. Exits non-zero on any mismatch.

Serves a temp copy of the given bundle so exploration's derived state never
lands in the fixture tree.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = Path(__file__).resolve().parents[1]
SEVEN = {"overview", "list", "search", "peek", "read", "links", "history"}


async def check(bundle: Path) -> None:
    transport = stdio_client(
        StdioServerParameters(
            command="uv",
            args=[
                "run",
                "--no-sync",
                "--project",
                str(REPO),
                "oknoll",
                "mcp",
                "stdio",
                str(bundle),
            ],
        )
    )
    async with Client(transport) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == SEVEN, f"tool surface mismatch: {names}"
        overview = await client.call_tool("overview", {})
        assert overview.is_error is False, overview.content
        payload = overview.structured_content or {}
        assert "counts" in payload, payload
        # a guardrail refusal must be a structured error, not a crash
        bad = await client.call_tool("read", {"path": "../../../etc/passwd"})
        assert bad.is_error is True, "traversal path was not refused"
    print("mcp_smoke: OK")


def main() -> int:
    src = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="oknoll-mcp-smoke-") as tmp:
        bundle = Path(tmp) / src.name
        shutil.copytree(src, bundle)
        anyio.run(check, bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
