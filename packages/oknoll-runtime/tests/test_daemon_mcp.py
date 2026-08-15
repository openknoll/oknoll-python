"""MCP protocol tests: the daemon's /mcp endpoint via the mcp client SDK.

Runs fully in-process (Streamable HTTP over an ASGI transport) so the tests
exercise the real protocol path — initialize, tool discovery, tool calls,
structured errors — without sockets.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from oknoll_runtime import Catalog, Store, install_bundle, runtime_dirs
from oknoll_runtime.daemon import build_app

REPO_ROOT = Path(__file__).resolve().parents[3]
MULTIHOP = REPO_ROOT / "fixtures" / "bundles" / "golden" / "multihop"
FOREIGN = REPO_ROOT / "fixtures" / "bundles" / "upstream" / "acme_retail"

BASE_URL = "http://127.0.0.1:7465"

EXPECTED_TOOLS = {
    "bundles_list",
    "bundles_get",
    "overview",
    "list",
    "search",
    "peek",
    "read",
    "links",
    "history",
}


def _install_fixtures() -> None:
    dirs = runtime_dirs()
    store, catalog = Store(dirs.data), Catalog(dirs.data)
    install_bundle(store, catalog, MULTIHOP, alias="golden")
    install_bundle(store, catalog, FOREIGN, alias="acme")


def _run(scenario: Callable[[ClientSession], Awaitable[None]]) -> None:
    """Drive one MCP client session against a fresh in-process daemon."""
    _install_fixtures()
    app = build_app(runtime_dirs(), today="2026-08-15")
    token = app.state.token

    async def main() -> None:
        async with app.router.lifespan_context(app):
            http = httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url=BASE_URL,
                headers={"Authorization": f"Bearer {token}"},
            )
            async with (
                streamable_http_client(f"{BASE_URL}/mcp", http_client=http) as (
                    read,
                    write,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                await scenario(session)

    anyio.run(main)


async def _text(result: Any) -> str:
    return str(result.content[0].text) if result.content else ""


def test_initialize_and_tool_surface() -> None:
    async def scenario(session: ClientSession) -> None:
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
        for tool in tools.tools:
            assert tool.annotations and tool.annotations.read_only_hint is True
            if tool.name not in ("bundles_list", "bundles_get"):
                assert "bundle" in (tool.input_schema.get("required") or []), tool.name

    _run(scenario)


def test_bundles_list_then_explore_two_bundles() -> None:
    async def scenario(session: ClientSession) -> None:
        listing = await session.call_tool("bundles_list", {})
        assert not listing.is_error
        text = await _text(listing)
        assert "golden@sha256:" in text and "acme@sha256:" in text

        for alias in ("golden", "acme"):
            overview = await session.call_tool("overview", {"bundle": alias})
            assert not overview.is_error, alias
            body = await _text(overview)
            assert f"{alias}@sha256:" in body  # qualified identity in every result

        search = await session.call_tool("search", {"bundle": "golden", "query": "incident"})
        assert not search.is_error
        assert "incident-response.md" in await _text(search)

    _run(scenario)


def test_bundle_argument_is_required() -> None:
    async def scenario(session: ClientSession) -> None:
        result = await session.call_tool("overview", {})
        assert result.is_error  # schema validation refuses the bundle-less call

    _run(scenario)


def test_refusals_are_clean_and_path_free() -> None:
    async def scenario(session: ClientSession) -> None:
        traversal = await session.call_tool("read", {"bundle": "golden", "path": "../etc/passwd"})
        assert traversal.is_error
        message = await _text(traversal)
        assert "escapes the bundle root" in message
        assert str(runtime_dirs().data) not in message

        unknown = await session.call_tool("overview", {"bundle": "nope"})
        assert unknown.is_error
        assert "no installed bundle named" in await _text(unknown)

    _run(scenario)


def test_unauthenticated_mcp_is_refused() -> None:
    _install_fixtures()
    app = build_app(runtime_dirs(), today="2026-08-15")

    async def main() -> None:
        async with app.router.lifespan_context(app):
            http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE_URL)
            # The SDK surfaces the transport 401 as a backend-specific
            # exception (often wrapped in an ExceptionGroup) — the contract
            # under test is only "no session without the token".
            with pytest.raises(Exception):  # noqa: B017
                async with streamable_http_client(f"{BASE_URL}/mcp", http_client=http) as (
                    read,
                    write,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()

    anyio.run(main)
