"""Web connector: shared contract suite + crawl-policy and normalization tests.

All tests run offline against the fixture site in fixtures/sources/website/,
served by a fake transport injected under the real SafeFetcher — so scheme,
address, redirect, robots, and rate-limit enforcement paths are the production
code paths.
"""

from pathlib import Path

import pytest
from okf_core import CanonicalDoc, SourceRef
from oknoll_connectors import ConnectorCheckpoint, ConnectorError, FetchPolicy, WebConnector
from oknoll_connectors.fetch import SafeFetcher, TransportRequest, TransportResponse
from oknoll_connectors.testing import ConnectorContractSuite, ConnectorFixture

REPO_ROOT = Path(__file__).resolve().parents[3]
SITE = REPO_ROOT / "fixtures" / "sources" / "website"

HOST = "example.test"
PUBLIC_IP = "93.184.216.34"
FIXED_CLOCK = "2026-08-03T00:00:00Z"

_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml",
}


class FakeSiteTransport:
    """Serves fixtures/sources/website/ for https://example.test; records
    every request so tests can assert what the crawler did (and did not) fetch."""

    def __init__(self, site_dir: Path = SITE) -> None:
        self.site_dir = site_dir
        self.requests: list[str] = []
        self.hosts: set[str] = set()
        self.redirects = {"/old.html": "/about.html"}

    def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
        assert request.ip == PUBLIC_IP, "transport must receive the validated IP"
        path = request.path.partition("?")[0]
        self.requests.append(path)
        self.hosts.add(request.host)

        if path in self.redirects:
            return TransportResponse(301, {"location": self.redirects[path]}, b"")
        rel = "index.html" if path == "/" else path.lstrip("/")
        file_path = self.site_dir / rel
        if not file_path.is_file():
            return TransportResponse(404, {"content-type": "text/plain"}, b"not found")
        data = file_path.read_bytes()[:max_bytes]
        media = _MEDIA_TYPES.get(file_path.suffix, "application/octet-stream")
        return TransportResponse(200, {"content-type": media}, data)


def make_web(
    checkpoint: ConnectorCheckpoint | None = None,
    *,
    transport: FakeSiteTransport | None = None,
    max_pages: int = 100,
) -> tuple[WebConnector, FakeSiteTransport]:
    fake = transport if transport is not None else FakeSiteTransport()
    connector = WebConnector(
        fetcher_factory=lambda policy: SafeFetcher(
            policy,
            transport=fake,
            resolver=lambda host: (PUBLIC_IP,),
            sleep=lambda seconds: None,
        ),
        max_pages=max_pages,
        clock=lambda: FIXED_CLOCK,
        checkpoint=checkpoint,
    )
    return connector, fake


SEED = SourceRef(connector="web", uri="https://example.test/")

EXPECTED_URIS = [
    "https://example.test/",
    "https://example.test/about.html",
    "https://example.test/changelog.md",
    "https://example.test/docs/setup.html",
    "https://example.test/docs/usage.html",
    "https://example.test/notes.txt",
]


class TestWebConnectorContract(ConnectorContractSuite):
    def make_fixture(self, tmp_path: Path) -> ConnectorFixture:
        return ConnectorFixture(
            make_connector=lambda checkpoint: make_web(checkpoint)[0],
            source=SEED,
            policy=FetchPolicy(),
            min_items=6,
            malformed_source=SourceRef(connector="web", uri="https://example.test/missing.html"),
        )


def _acquire_docs() -> dict[str, CanonicalDoc]:
    connector, _ = make_web()
    items = list(connector.acquire(SEED, FetchPolicy()))
    return {item.uri: next(iter(connector.normalize(item))) for item in items}


def test_crawl_yields_expected_pages_sorted() -> None:
    connector, _ = make_web()
    items = list(connector.acquire(SEED, FetchPolicy()))
    assert [item.uri for item in items] == EXPECTED_URIS


def test_robots_disallowed_path_is_never_requested() -> None:
    connector, transport = make_web()
    list(connector.acquire(SEED, FetchPolicy()))
    assert not any(path.startswith("/private/") for path in transport.requests)


def test_crawl_stays_on_site() -> None:
    connector, transport = make_web()
    list(connector.acquire(SEED, FetchPolicy()))
    assert transport.hosts == {HOST}


def test_sitemap_urls_are_crawled_before_discovered_links() -> None:
    connector, transport = make_web()
    list(connector.acquire(SEED, FetchPolicy()))
    assert transport.requests[:3] == ["/robots.txt", "/sitemap.xml", "/"]
    sitemap_pages = ["/changelog.md", "/docs/setup.html", "/docs/usage.html"]
    assert transport.requests[3:6] == sitemap_pages
    assert transport.requests.index("/about.html") > transport.requests.index("/docs/usage.html")


def test_redirect_target_is_deduplicated() -> None:
    connector, transport = make_web()
    items = list(connector.acquire(SEED, FetchPolicy()))
    assert "/old.html" in transport.requests  # the redirect source was followed
    uris = [item.uri for item in items]
    assert uris.count("https://example.test/about.html") == 1


def test_max_pages_caps_the_crawl() -> None:
    connector, _ = make_web(max_pages=2)
    items = list(connector.acquire(SEED, FetchPolicy()))
    assert len(items) == 2


def test_seed_disallowed_by_robots_raises() -> None:
    connector, _ = make_web()
    with pytest.raises(ConnectorError, match="robots"):
        list(
            connector.acquire(
                SourceRef(connector="web", uri="https://example.test/private/secret.html"),
                FetchPolicy(),
            )
        )


def test_html_normalization() -> None:
    docs = _acquire_docs()
    doc = docs["https://example.test/"]
    assert doc.title == "Example Docs"
    assert [a.id for a in doc.anchors] == ["example-docs", "more-reading"]
    kinds = [b.kind for b in doc.blocks]
    assert kinds == ["heading", "paragraph", "heading", "list"]
    targets = {link.target for link in doc.links}
    assert "https://example.test/docs/setup.html" in targets
    assert "https://other.example/offsite.html" in targets  # captured, never crawled
    assert not any(target.startswith("mailto:") for target in targets)
    assert "console.log" not in " ".join(b.text for b in doc.blocks)

    setup = docs["https://example.test/docs/setup.html"]
    assert any(b.kind == "code" and "oknoll init" in b.text for b in setup.blocks)
    assert any(b.kind == "list" for b in setup.blocks)


def test_markdown_and_text_normalization() -> None:
    docs = _acquire_docs()
    changelog = docs["https://example.test/changelog.md"]
    assert changelog.title == "Changelog"
    assert [a.id for a in changelog.anchors] == ["changelog", "0-2-0", "0-1-0"]

    notes = docs["https://example.test/notes.txt"]
    assert notes.title == "notes"
    assert [a.kind for a in notes.anchors] == ["line", "line", "line"]


def test_probe_is_offline_and_shape_based() -> None:
    connector, transport = make_web()
    result = connector.probe(SEED)
    assert result.supported and result.kind == "url"
    assert transport.requests == []
    assert not connector.probe(SourceRef(connector="web", uri="ftp://example.test/")).supported
    assert not connector.probe(SourceRef(connector="web", uri="notaurl")).supported


def test_checkpoint_from_other_connector_rejected() -> None:
    foreign = ConnectorCheckpoint(connector="files", version="0.1.0", cursor={"last_uri": "x"})
    with pytest.raises(ConnectorError, match="belongs to connector"):
        WebConnector(checkpoint=foreign)
