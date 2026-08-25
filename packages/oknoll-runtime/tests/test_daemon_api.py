"""HTTP contract tests for the daemon's /api/v1 surface.

Covers the response shapes, the {code, message, details} error schema, and
the session-pinning contract: sessions keep the content identity they were
created with, whatever happens to the alias afterwards.
"""

from __future__ import annotations

import concurrent.futures
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from okf_core import StubModelProvider
from oknoll_runtime import Catalog, Store, install_bundle, runtime_dirs
from oknoll_runtime.daemon import build_app
from starlette.applications import Starlette
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
MULTIHOP = REPO_ROOT / "fixtures" / "bundles" / "golden" / "multihop"
MINIMAL = REPO_ROOT / "fixtures" / "bundles" / "golden" / "minimal"
FOREIGN = REPO_ROOT / "fixtures" / "bundles" / "upstream" / "acme_retail"


def _install(source: Path, alias: str, *, update: bool = False) -> None:
    dirs = runtime_dirs()
    install_bundle(Store(dirs.data), Catalog(dirs.data), source, alias=alias, update=update)


@pytest.fixture()
def app() -> Iterator[Starlette]:
    # The repo-root conftest pins OKNOLL_HOME to a tmp dir per test.
    _install(MULTIHOP, "golden")
    _install(FOREIGN, "acme")
    application = build_app(
        runtime_dirs(),
        provider_resolver=lambda spec: StubModelProvider(),
        today="2026-08-15",
    )
    yield application


@pytest.fixture()
def client(app: Starlette) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        test_client.headers["Authorization"] = f"Bearer {app.state.token}"
        yield test_client


def test_health_and_catalog(client: TestClient) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["bundles"] == 2

    catalog = client.get("/api/v1/catalog")
    assert catalog.status_code == 200
    bundles = catalog.json()["bundles"]
    assert [b["alias"] for b in bundles] == ["acme", "golden"]
    for bundle in bundles:
        assert bundle["bundle"] == f"{bundle['alias']}@{bundle['oci_digest']}"
        assert bundle["oci_digest"].startswith("sha256:")
        assert bundle["okf_revision"].startswith("rev-")
        assert isinstance(bundle["lint_summary"], dict)


def test_seven_tools_over_http(client: TestClient) -> None:
    overview = client.get("/api/v1/bundles/golden/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["bundle"].startswith("golden@sha256:")
    assert body["okf_revision"].startswith("rev-")
    assert body["counts"]["concepts"] >= 1

    listing = client.get("/api/v1/bundles/golden/list?directory=concepts&limit=2")
    assert listing.status_code == 200
    assert len(listing.json()["entries"]) <= 2

    search = client.get("/api/v1/bundles/golden/search?query=incident")
    assert search.status_code == 200
    hits = search.json()["results"]
    assert hits and any("incident" in h["path"] for h in hits)
    path = hits[0]["path"]

    for tool, params in (
        ("peek", f"path={path}&lines=5"),
        ("read", f"path={path}"),
        ("links", f"path={path}&direction=both"),
        ("history", "limit=5"),
    ):
        response = client.get(f"/api/v1/bundles/golden/{tool}?{params}")
        assert response.status_code == 200, (tool, response.text)
        assert response.json()["bundle"].startswith("golden@sha256:")


def test_error_schema_and_bounds(client: TestClient) -> None:
    unknown_bundle = client.get("/api/v1/bundles/nope/overview")
    assert unknown_bundle.status_code == 404
    body = unknown_bundle.json()
    assert set(body) == {"code", "message", "details"}
    assert body["code"] == "unknown_bundle"

    unknown_tool = client.get("/api/v1/bundles/golden/drop")
    assert unknown_tool.status_code == 404
    assert unknown_tool.json()["code"] == "unknown_tool"

    traversal = client.get("/api/v1/bundles/golden/read?path=../../etc/passwd")
    assert traversal.status_code == 400
    assert traversal.json()["code"] == "refused"
    assert (
        "/etc/passwd" not in traversal.json()["message"]
        or "escapes" in (traversal.json()["message"])
    )

    missing_param = client.get("/api/v1/bundles/golden/read")
    assert missing_param.status_code == 400
    assert missing_param.json()["code"] == "bad_request"

    bad_int = client.get("/api/v1/bundles/golden/list?limit=zap")
    assert bad_int.status_code == 400

    bad_json = client.post(
        "/api/v1/ask", content=b"{nope", headers={"Content-Type": "application/json"}
    )
    assert bad_json.status_code == 400


def test_ask_answers_with_qualified_citations(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ask",
        json={"bundle": "golden", "question": "What is the incident response process?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"].startswith("golden@sha256:")
    assert body["citations"], body
    for citation in body["citations"]:
        assert citation["qualified"].startswith(body["bundle"] + "/")
    # No host paths anywhere in the payload.
    assert str(runtime_dirs().data) not in response.text
    assert body["trace"]["id"].startswith("ask-")


def test_session_pins_content_across_alias_repoint(client: TestClient) -> None:
    created = client.post("/api/v1/sessions", json={"bundle": "golden"})
    assert created.status_code == 201
    session = created.json()
    pinned_digest = session["oci_digest"]

    # Repoint the alias at different content — the running session must not move.
    _install(MINIMAL, "golden", update=True)

    turn = client.post(
        "/api/v1/ask", json={"session": session["session"], "question": "incident process?"}
    )
    assert turn.status_code == 200
    assert turn.json()["bundle"] == f"golden@{pinned_digest}"

    fresh = client.post("/api/v1/sessions", json={"bundle": "golden"})
    assert fresh.status_code == 201
    assert fresh.json()["oci_digest"] != pinned_digest


def test_two_concurrent_clients_keep_their_pins(app: Starlette) -> None:
    token = app.state.token
    # Only one client enters the lifespan (the MCP session manager runs once
    # per app); the second is a plain HTTP peer over the same daemon.
    with TestClient(app) as one:
        two = TestClient(app)
        for client in (one, two):
            client.headers["Authorization"] = f"Bearer {token}"
        first = one.post("/api/v1/sessions", json={"bundle": "golden"}).json()
        _install(MINIMAL, "golden", update=True)
        second = two.post("/api/v1/sessions", json={"bundle": "golden"}).json()
        assert first["oci_digest"] != second["oci_digest"]

        def turn(client: TestClient, session: dict[str, str]) -> str:
            response = client.post(
                "/api/v1/ask",
                json={"session": session["session"], "question": "what is this bundle?"},
            )
            assert response.status_code == 200, response.text
            return str(response.json()["bundle"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(turn, one, first), pool.submit(turn, two, second)]
            identities = [f.result() for f in futures]
    assert identities[0] == f"golden@{first['oci_digest']}"
    assert identities[1] == f"golden@{second['oci_digest']}"


def test_session_mode_mismatch_and_unknown_session(client: TestClient) -> None:
    created = client.post("/api/v1/sessions", json={"bundle": "golden", "mode": "pd"}).json()
    mismatch = client.post(
        "/api/v1/ask",
        json={"session": created["session"], "question": "q", "mode": "rag"},
    )
    assert mismatch.status_code == 400

    unknown = client.post("/api/v1/ask", json={"session": "feedbead", "question": "q"})
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "unknown_session"


def test_index_cache_lands_in_the_cache_plane(client: TestClient) -> None:
    client.get("/api/v1/bundles/golden/search?query=incident")
    index_root = runtime_dirs().cache / "index"
    assert index_root.is_dir() and any(index_root.iterdir())
    # Nothing was written into the immutable store tree.
    store_root = runtime_dirs().data / "store"
    if store_root.is_dir():
        assert not list(store_root.rglob(".oknoll/index/*"))


def test_ask_without_provider_is_501(app: Starlette) -> None:
    bare = build_app(runtime_dirs(), today="2026-08-15", token="secondary-token")
    with TestClient(bare) as client:
        client.headers["Authorization"] = "Bearer secondary-token"
        response = client.post("/api/v1/ask", json={"bundle": "golden", "question": "q"})
    assert response.status_code == 501
    assert response.json()["code"] == "not_configured"


def test_paged_read_over_http(client: TestClient) -> None:
    search = client.get("/api/v1/bundles/golden/search?query=incident")
    path = search.json()["results"][0]["path"]

    full = client.get(f"/api/v1/bundles/golden/read?path={path}").json()
    assert full["truncated"] is False and full["next_start"] is None

    # Page through the same body in bounded calls; concatenation is lossless.
    pages: list[str] = []
    start = 0
    while True:
        page = client.get(
            f"/api/v1/bundles/golden/read?path={path}&max_chars=64&start_char={start}"
        ).json()
        assert page["body_total_chars"] == full["body_total_chars"]
        pages.append(page["body"])
        if page["next_start"] is None:
            break
        assert page["truncated"] is True
        start = page["next_start"]
    assert "".join(pages) == full["body"]


def test_session_conversation_context_carries_between_turns(client: TestClient) -> None:
    created = client.post("/api/v1/sessions", json={"bundle": "golden", "mode": "pd"})
    session = created.json()["session"]

    first = client.post(
        "/api/v1/ask",
        json={"session": session, "question": "What is the incident response process?"},
    ).json()
    first_trace = json.loads(
        (runtime_dirs().data / "traces" / "golden" / first["trace"]["id"]).read_text(
            encoding="utf-8"
        )
    )
    assert "conversation" not in first_trace["trace"]

    second = client.post(
        "/api/v1/ask", json={"session": session, "question": "What are its limitations?"}
    ).json()
    assert second["turns"] == 2
    second_trace = json.loads(
        (runtime_dirs().data / "traces" / "golden" / second["trace"]["id"]).read_text(
            encoding="utf-8"
        )
    )
    conversation = second_trace["trace"]["conversation"]
    assert conversation["prior_turns"] == 1
    # The first answer's citations seeded the second turn's retrieval.
    assert conversation["carryover_paths"] == [c["path"] for c in first["citations"]]

    # A bundle-addressed ask (no session) stays conversation-blind.
    bare = client.post(
        "/api/v1/ask", json={"bundle": "golden", "question": "incident process?"}
    ).json()
    bare_trace = json.loads(
        (runtime_dirs().data / "traces" / "golden" / bare["trace"]["id"]).read_text(
            encoding="utf-8"
        )
    )
    assert "conversation" not in bare_trace["trace"]
