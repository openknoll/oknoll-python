"""SSRF corpus — release gate (design §6.3/§17, implementation plan Phase 3).

Every URL in fixtures/security/ssrf/cases.json must be refused by SafeFetcher
*before any connection is attempted*: the sentinel transport fails the test if
it is ever reached. Positive controls must stay fetchable, so the guard cannot
"pass" by blocking everything. Additional tests pin the rest of the FetchPolicy
envelope: redirect re-validation, redirect caps, response size caps, bounded
decompression, and per-host rate limiting.
"""

import gzip
import json
from pathlib import Path

import pytest
from oknoll_connectors import FetchPolicy
from oknoll_connectors.fetch import (
    BlockedUrlError,
    FetchError,
    SafeFetcher,
    TransportRequest,
    TransportResponse,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = json.loads((REPO_ROOT / "fixtures" / "security" / "ssrf" / "cases.json").read_text())

PUBLIC_IP = "93.184.216.34"


def corpus_resolver(host: str) -> tuple[str, ...]:
    return tuple(CORPUS["dns"].get(host, [PUBLIC_IP]))


class SentinelTransport:
    """Fails the test if a blocked URL ever produces a connection attempt."""

    def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
        raise AssertionError(
            f"SSRF guard breached: transport reached for {request.scheme}://"
            f"{request.host}{request.path} (ip {request.ip})"
        )


class OkTransport:
    def __init__(self) -> None:
        self.requests: list[TransportRequest] = []

    def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
        self.requests.append(request)
        return TransportResponse(200, {"content-type": "text/html"}, b"<html>ok</html>")


def make_fetcher(
    transport: object,
    policy: FetchPolicy | None = None,
    *,
    clock: object | None = None,
    sleep: object | None = None,
) -> SafeFetcher:
    return SafeFetcher(
        policy or FetchPolicy(),
        transport=transport,  # type: ignore[arg-type]
        resolver=corpus_resolver,
        clock=clock,  # type: ignore[arg-type]
        sleep=sleep if sleep is not None else (lambda seconds: None),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("case", CORPUS["blocked"], ids=[c["url"] for c in CORPUS["blocked"]])
def test_corpus_url_is_blocked_before_any_connection(case: dict[str, str]) -> None:
    fetcher = make_fetcher(SentinelTransport())
    with pytest.raises(BlockedUrlError):
        fetcher.fetch(case["url"])


@pytest.mark.parametrize("case", CORPUS["allowed"], ids=[c["url"] for c in CORPUS["allowed"]])
def test_positive_controls_stay_fetchable(case: dict[str, str]) -> None:
    transport = OkTransport()
    response = make_fetcher(transport).fetch(case["url"])
    assert response.status == 200
    assert len(transport.requests) == 1
    assert transport.requests[0].ip in {PUBLIC_IP, transport.requests[0].host.strip("[]")}


def test_transport_receives_validated_ip_and_original_host() -> None:
    transport = OkTransport()
    make_fetcher(transport).fetch("https://example.com/page")
    request = transport.requests[0]
    assert request.ip == PUBLIC_IP
    assert request.host == "example.com"  # Host header / SNI keep the name


class RedirectTransport:
    """Serves a redirect chain; anything else 200s."""

    def __init__(self, redirects: dict[str, str]) -> None:
        self.redirects = redirects
        self.paths: list[str] = []

    def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
        self.paths.append(request.path)
        target = self.redirects.get(request.path)
        if target is not None:
            return TransportResponse(302, {"location": target}, b"")
        return TransportResponse(200, {"content-type": "text/plain"}, b"ok")


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://internal.corp.example/",
        "file:///etc/passwd",
    ],
)
def test_redirect_hops_are_revalidated(location: str) -> None:
    transport = RedirectTransport({"/start": location})
    fetcher = make_fetcher(transport)
    with pytest.raises(BlockedUrlError):
        fetcher.fetch("http://example.com/start")
    # only the public hop was ever contacted
    assert transport.paths == ["/start"]


def test_redirect_cap_is_enforced() -> None:
    transport = RedirectTransport({"/loop": "http://example.com/loop"})
    fetcher = make_fetcher(transport, FetchPolicy(max_redirects=3))
    with pytest.raises(FetchError, match="max_redirects"):
        fetcher.fetch("http://example.com/loop")
    assert len(transport.paths) == 4  # initial request + 3 followed redirects


def test_response_size_cap_is_enforced() -> None:
    class BigTransport:
        def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
            return TransportResponse(200, {"content-type": "text/html"}, b"x" * max_bytes)

    fetcher = make_fetcher(BigTransport(), FetchPolicy(max_item_bytes=1024))
    with pytest.raises(FetchError, match="max_item_bytes"):
        fetcher.fetch("http://example.com/big")


def test_decompression_bomb_is_bounded() -> None:
    bomb = gzip.compress(b"0" * (1024 * 1024))  # ~1 MiB inflated, ~1 KiB on the wire

    class BombTransport:
        def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
            return TransportResponse(
                200, {"content-type": "text/html", "content-encoding": "gzip"}, bomb
            )

    fetcher = make_fetcher(BombTransport(), FetchPolicy(max_decompressed_bytes=64 * 1024))
    with pytest.raises(FetchError, match="max_decompressed_bytes"):
        fetcher.fetch("http://example.com/bomb")


def test_gzip_within_limits_is_decoded() -> None:
    payload = b"<html>compressed page</html>"

    class GzipTransport:
        def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
            return TransportResponse(
                200,
                {"content-type": "text/html", "content-encoding": "gzip"},
                gzip.compress(payload),
            )

    response = make_fetcher(GzipTransport()).fetch("http://example.com/")
    assert response.data == payload


def test_allow_private_addresses_is_an_explicit_opt_in() -> None:
    transport = OkTransport()
    policy = FetchPolicy(allow_private_addresses=True)
    response = make_fetcher(transport, policy).fetch("http://127.0.0.1:8000/local")
    assert response.status == 200
    assert transport.requests[0].ip == "127.0.0.1"


def test_per_host_rate_limit_sleeps_between_requests() -> None:
    sleeps: list[float] = []
    ticks = iter(x * 0.1 for x in range(100))
    transport = OkTransport()
    fetcher = make_fetcher(
        transport,
        FetchPolicy(per_host_requests_per_second=1.0),
        clock=lambda: next(ticks),
        sleep=sleeps.append,
    )
    fetcher.fetch("http://example.com/a")
    fetcher.fetch("http://example.com/b")
    assert sleeps and sleeps[0] > 0  # second request to the same host had to wait
