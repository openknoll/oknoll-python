"""Assemble the daemon's Starlette application.

One loopback origin, three protocol surfaces:

    /          the minimal static web UI (one page, no build step)
    /api/v1/   the HTTP/JSON API
    /mcp       MCP Streamable HTTP (multi-bundle)

`GuardMiddleware` fronts everything: loopback-origin allowlist on all
requests, bearer token on `/api` and `/mcp`. The MCP sub-application brings
its own lifespan (the Streamable HTTP session manager), which the outer app
propagates.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from importlib import resources
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Mount, Route

from oknoll_runtime.daemon.api import build_api_routes
from oknoll_runtime.daemon.mcp import build_multi_server
from oknoll_runtime.daemon.security import GuardMiddleware, ensure_token
from oknoll_runtime.daemon.service import DaemonService, EmbedderResolver, ProviderResolver
from oknoll_runtime.dirs import RuntimeDirs, runtime_dirs


def _ui_page() -> str:
    return resources.files("oknoll_runtime.daemon").joinpath("ui/index.html").read_text("utf-8")


def build_app(
    dirs: RuntimeDirs | None = None,
    *,
    provider_resolver: ProviderResolver | None = None,
    embedder_resolver: EmbedderResolver | None = None,
    today: str | None = None,
    token: str | None = None,
) -> Starlette:
    """The daemon application; creates the bearer token on first build."""
    dirs = dirs or runtime_dirs()
    service = DaemonService(
        dirs,
        provider_resolver=provider_resolver,
        embedder_resolver=embedder_resolver,
        today=today,
    )
    bearer = token if token is not None else ensure_token(dirs.state)

    mcp_server = build_multi_server(service)
    # The MCP sub-app keeps its native "/mcp" route and mounts as the root
    # fall-through, so clients POST exactly `/mcp` — a slash-redirect here
    # would break Streamable HTTP clients, which do not follow redirects.
    mcp_asgi = mcp_server.streamable_http_app(streamable_http_path="/mcp")

    page = _ui_page()

    async def ui(request: Request) -> Response:
        return HTMLResponse(page)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with mcp_asgi.router.lifespan_context(mcp_asgi):
            yield

    app = Starlette(
        routes=[
            Route("/", ui, methods=["GET"]),
            Mount("/api/v1", routes=build_api_routes(service)),
            Mount("/", app=mcp_asgi),
        ],
        middleware=[Middleware(GuardMiddleware, token=bearer)],
        lifespan=lifespan,
    )
    # Handles for tests and the serving CLI; never part of the HTTP surface.
    app.state.service = service
    app.state.token = bearer
    app.state.mcp_server = mcp_server
    return app


def app_factory(**kwargs: Any) -> Starlette:
    """Uvicorn-friendly zero-argument factory (default runtime directories)."""
    return build_app(**kwargs)
