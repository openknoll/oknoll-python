"""OllamaEmbedder over httpx.MockTransport — fully offline."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest
from oknoll_providers import OllamaEmbedder, ProviderError, resolve_embedder


def test_request_shape_and_normalization() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0], [0.0, 0.0]]})

    embedder = OllamaEmbedder("nomic-embed-text", transport=httpx.MockTransport(handler))
    vectors = embedder.embed(["alpha", "beta"])

    assert seen["url"] == "http://127.0.0.1:11434/api/embed"
    assert seen["body"] == {"model": "nomic-embed-text", "input": ["alpha", "beta"]}
    assert embedder.id == "ollama:nomic-embed-text"
    assert vectors[0] == [0.6, 0.8]  # L2-normalized on ingest
    assert math.isclose(sum(v * v for v in vectors[0]), 1.0, rel_tol=1e-9)
    assert vectors[1] == [0.0, 0.0]  # zero vector stays zero, no division blowup


def test_empty_input_never_calls_the_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request expected for empty input")

    assert OllamaEmbedder("m", transport=httpx.MockTransport(handler)).embed([]) == []


def test_http_error_is_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    embedder = OllamaEmbedder("missing", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="ollama pull missing"):
        embedder.embed(["alpha"])


def test_count_mismatch_is_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    embedder = OllamaEmbedder("m", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="unexpected response shape"):
        embedder.embed(["alpha", "beta"])


def test_resolve_embedder_spec_grammar() -> None:
    assert resolve_embedder("stub").id == "stub"
    assert resolve_embedder("ollama:nomic-embed-text").id == "ollama:nomic-embed-text"
    with pytest.raises(ValueError, match="explicit model"):
        resolve_embedder("ollama:")
    with pytest.raises(ValueError, match="unknown embedding provider"):
        resolve_embedder("anthropic:voyage")
