"""OllamaProvider over httpx.MockTransport — fully offline."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from oknoll_providers import OllamaProvider, ProviderError

CONCEPT_PAYLOAD = {
    "title": "Release process",
    "excerpt": "Releases ship weekly.",
    "content_hash": "abc",
}


def _chat_body(content: str) -> dict[str, Any]:
    return {
        "model": "llama3",
        "created_at": "2026-08-05T00:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
    }


def test_request_shape() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_body("A grounded description."))

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    text = provider.complete("concept-description", CONCEPT_PAYLOAD)

    assert text == "A grounded description."
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    body = seen["body"]
    assert body["model"] == "llama3"
    assert body["stream"] is False
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert "Releases ship weekly." in body["messages"][1]["content"]


def test_host_from_env_and_scheme_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "localhost:9999/")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_chat_body("ok"))

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    provider.complete("concept-description", CONCEPT_PAYLOAD)
    assert seen["url"] == "http://localhost:9999/api/chat"


def test_connect_error_hints_at_ollama_serve() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="ollama serve"):
        provider.complete("concept-description", CONCEPT_PAYLOAD)


def test_http_error_mentions_pulling_the_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'llama3' not found"})

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="ollama pull llama3"):
        provider.complete("concept-description", CONCEPT_PAYLOAD)


def test_empty_completion_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_body("   "))

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="empty completion"):
        provider.complete("concept-description", CONCEPT_PAYLOAD)


def test_non_json_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="not valid JSON"):
        provider.complete("concept-description", CONCEPT_PAYLOAD)


def test_truncated_completion_never_returns_partial_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _chat_body("This answer was cut off mid-sent")
        body["done_reason"] = "length"
        return httpx.Response(200, json=body)

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="did not finish cleanly"):
        provider.complete("concept-description", CONCEPT_PAYLOAD)


def test_unfinished_completion_never_returns_partial_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _chat_body("partial")
        body["done"] = False
        return httpx.Response(200, json=body)

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="did not finish cleanly"):
        provider.complete("concept-description", CONCEPT_PAYLOAD)


def test_connect_error_redacts_userinfo_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://user:supersecret@ollama.internal:11434")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider("llama3", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as excinfo:
        provider.complete("concept-description", CONCEPT_PAYLOAD)
    assert "supersecret" not in str(excinfo.value)
    assert "ollama.internal:11434" in str(excinfo.value)


def test_close_releases_the_http_client() -> None:
    provider = OllamaProvider(
        "llama3", transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    provider.close()
