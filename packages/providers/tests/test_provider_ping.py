"""`ping_ollama` liveness probe — offline via httpx.MockTransport."""

import httpx
import pytest
from oknoll_providers import ProviderError, ping_ollama


def _transport(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


def test_ping_returns_server_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        return httpx.Response(200, json={"version": "0.5.1"})

    assert ping_ollama("http://box:11434", transport=_transport(handler)) == "0.5.1"


def test_ping_unversioned_response_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    assert ping_ollama("http://box:11434", transport=_transport(handler)) == "unknown"


def test_ping_http_error_names_the_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(ProviderError, match="HTTP 500"):
        ping_ollama("http://box:11434", transport=_transport(handler))


def test_ping_connect_error_suggests_ollama_serve() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError, match="is `ollama serve` running"):
        ping_ollama("http://user:secret@box:11434", transport=_transport(handler))


def test_ping_strips_credentials_from_error_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError) as excinfo:
        ping_ollama("http://user:secret@box:11434", transport=_transport(handler))
    assert "secret" not in str(excinfo.value)
