"""Daemon security posture: bearer token, origin validation, loopback-only.

The daemon binds loopback only, but loopback is not authorization: any local
browser tab can reach 127.0.0.1, so `/api` and `/mcp` require a random bearer
token created at first start and stored 0600 in the state directory — only
the operating-system user who owns the daemon can read it. Browser requests
additionally pass an Origin allowlist (loopback origins only), which stops a
hostile web page from riding the user's browser into the daemon (DNS
rebinding / CSRF); non-browser clients send no Origin header and are
governed by the token alone. The UI page itself is served without the token
— it is static and content-free until it authenticates.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

TOKEN_FILENAME = "daemon.token"

# Loopback origins only, any port. A browser page served from anywhere else
# must not be able to script this daemon.
_ALLOWED_ORIGIN_HOSTS = ("127.0.0.1", "localhost", "[::1]")

_PROTECTED_PREFIXES = ("/api", "/mcp")


def token_path(state_dir: Path) -> Path:
    return state_dir / TOKEN_FILENAME


def ensure_token(state_dir: Path) -> str:
    """Return the daemon bearer token, creating it 0600 on first use."""
    path = token_path(state_dir)
    existing = read_token(state_dir)
    if existing is not None:
        return existing
    state_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return token


def read_token(state_dir: Path) -> str | None:
    path = token_path(state_dir)
    try:
        token = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token or None


def origin_allowed(origin: str) -> bool:
    scheme, _, rest = origin.partition("://")
    if scheme != "http":
        return False
    host = rest.split("/", 1)[0]
    # An IPv6 literal keeps its brackets; otherwise strip the port.
    host = host.split("]", 1)[0] + "]" if host.startswith("[") else host.split(":", 1)[0]
    return host in _ALLOWED_ORIGIN_HOSTS


class GuardMiddleware:
    """Raw ASGI middleware: Origin allowlist everywhere, bearer token on
    ``/api`` and ``/mcp``.

    Raw ASGI (not ``BaseHTTPMiddleware``) so the MCP Streamable HTTP
    responses stream through untouched. Failure responses are minimal JSON in
    the daemon's ``{code, message, details}`` error schema and never echo
    request content.
    """

    def __init__(self, app: Any, *, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }

        origin = headers.get("origin")
        if origin is not None and not origin_allowed(origin):
            await _refuse(send, 403, "forbidden_origin", "origin not allowed")
            return

        path: str = scope.get("path", "")
        if path.startswith(_PROTECTED_PREFIXES):
            supplied = headers.get("authorization", "")
            expected = f"Bearer {self.token}"
            if not secrets.compare_digest(supplied, expected):
                await _refuse(send, 401, "unauthorized", "missing or invalid bearer token")
                return

        await self.app(scope, receive, send)


async def _refuse(send: Any, status: int, code: str, message: str) -> None:
    body = (
        '{"code": "%s", "message": "%s", "details": {}}' % (code, message)  # noqa: UP031
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
