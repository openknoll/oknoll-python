"""Daemon security posture — release gate (runs in `make security`).

Pins the ADR-decided posture: loopback-only bind, random 0600 bearer token
required on /api and /mcp, loopback-origin allowlist on everything, and
client-visible errors that never contain host paths.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from oknoll_runtime import Catalog, Store, install_bundle, runtime_dirs
from oknoll_runtime.daemon import build_app, ensure_token, read_token, serve_foreground
from oknoll_runtime.daemon.security import origin_allowed
from starlette.applications import Starlette
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
MULTIHOP = REPO_ROOT / "fixtures" / "bundles" / "golden" / "multihop"


@pytest.fixture()
def app() -> Iterator[Starlette]:
    dirs = runtime_dirs()
    install_bundle(Store(dirs.data), Catalog(dirs.data), MULTIHOP, alias="golden")
    yield build_app(dirs, today="2026-08-15")


def test_token_file_is_0600_and_stable() -> None:
    state = runtime_dirs().state
    token = ensure_token(state)
    assert len(token) >= 32
    mode = stat.S_IMODE(os.stat(state / "daemon.token").st_mode)
    assert mode == 0o600
    assert ensure_token(state) == token  # stable across restarts
    assert read_token(state) == token


def test_api_and_mcp_require_the_bearer_token(app: Starlette) -> None:
    with TestClient(app) as client:
        for path in ("/api/v1/health", "/api/v1/catalog", "/api/v1/bundles/golden/overview"):
            assert client.get(path).status_code == 401, path
            wrong = client.get(path, headers={"Authorization": "Bearer nope"})
            assert wrong.status_code == 401, path
        refused = client.post("/mcp", json={})
        assert refused.status_code == 401

        ok = client.get("/api/v1/health", headers={"Authorization": f"Bearer {app.state.token}"})
        assert ok.status_code == 200

        body = refused.json()
        assert set(body) == {"code", "message", "details"}
        assert body["code"] == "unauthorized"


def test_ui_is_served_without_token_and_leaks_nothing(app: Starlette) -> None:
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert app.state.token not in page.text
        assert str(runtime_dirs().data) not in page.text


def test_hostile_origins_are_refused_even_with_a_valid_token(app: Starlette) -> None:
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {app.state.token}"}
        for origin in (
            "http://evil.example",
            "https://127.0.0.1.evil.example",
            "http://127.0.0.1.evil.example:7465",
            "null",
            "https://127.0.0.1",  # https never served locally
        ):
            for path in ("/", "/api/v1/catalog", "/mcp"):
                response = client.get(path, headers={**headers, "Origin": origin})
                assert response.status_code == 403, (origin, path)

        for origin in (
            "http://127.0.0.1:7465",
            "http://localhost:7465",
            "http://[::1]:7465",
            "http://127.0.0.1",
        ):
            response = client.get("/api/v1/catalog", headers={**headers, "Origin": origin})
            assert response.status_code == 200, origin


def test_origin_allowlist_is_loopback_only() -> None:
    assert origin_allowed("http://127.0.0.1:7465")
    assert origin_allowed("http://localhost")
    assert origin_allowed("http://[::1]:9999")
    assert not origin_allowed("http://evil.example")
    assert not origin_allowed("http://localhost.evil.example")
    assert not origin_allowed("https://localhost")
    assert not origin_allowed("file://localhost")
    assert not origin_allowed("")


def test_client_errors_never_contain_host_paths(app: Starlette) -> None:
    dirs = runtime_dirs()
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {app.state.token}"
        probes = (
            "/api/v1/bundles/golden/read?path=../../../etc/passwd",
            "/api/v1/bundles/golden/read?path=/.oknoll/index",
            "/api/v1/bundles/nope/overview",
            "/api/v1/bundles/golden/list?directory=../..",
            "/api/v1/bundles/golden/peek",
        )
        for probe in probes:
            response = client.get(probe)
            assert response.status_code in (400, 404), probe
            text = response.text
            for secret in (str(dirs.data), str(dirs.state), str(Path.home()), "/var/"):
                assert secret not in text, (probe, secret)


def test_serve_binds_loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    import uvicorn

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    serve_foreground(object(), port=7465, state_dir=runtime_dirs().state)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 7465
