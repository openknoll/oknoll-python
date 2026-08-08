"""Local Ollama embedding provider behind the okf-core EmbeddingProvider seam.

Talks to Ollama's REST API (``POST /api/embed``) over httpx with an injectable
transport, so tests run fully offline. Vectors are L2-normalized on ingest so
okf-core's dot-product retrieval is a true cosine regardless of what the model
returns. The model is folded into ``id`` (``ollama:<model>``), which keys the
on-disk vector index per embedder (design §9.4 freezes the embedding model).

Anthropic has no embeddings API, so Ollama is the only real embedder; the
deterministic CI embedder is okf-core's ``StubEmbeddingProvider``.
"""

from __future__ import annotations

import json
import math
import os

import httpx

from oknoll_providers.errors import ProviderError
from oknoll_providers.ollama_provider import DEFAULT_OLLAMA_HOST, _display_host, _normalize_host

_DEFAULT_TIMEOUT_SECONDS = 300.0


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector] if norm else vector


class OllamaEmbedder:
    """Batch text embeddings via a local Ollama server."""

    id: str

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.id = f"ollama:{model}"
        self._model = model
        self._host = _normalize_host(host or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST)
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.post(
                f"{self._host}/api/embed", json={"model": self._model, "input": texts}
            )
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"cannot reach Ollama at {_display_host(self._host)} — is `ollama serve` running?"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama embed request failed: {exc}") from exc

        if response.status_code != 200:
            snippet = response.text.strip()[:200]
            raise ProviderError(
                f"Ollama returned HTTP {response.status_code} for embedding model "
                f"{self._model!r}{f': {snippet}' if snippet else ''} — "
                f"if the model is missing, run `ollama pull {self._model}`"
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("Ollama returned a response that is not valid JSON") from exc

        embeddings = data.get("embeddings") if isinstance(data, dict) else None
        if (
            not isinstance(embeddings, list)
            or len(embeddings) != len(texts)
            or not all(isinstance(vector, list) for vector in embeddings)
        ):
            raise ProviderError(
                f"Ollama returned {len(embeddings) if isinstance(embeddings, list) else 'no'} "
                f"embedding(s) for {len(texts)} input(s) — unexpected response shape"
            )
        return [_l2_normalize([float(component) for component in vector]) for vector in embeddings]

    def close(self) -> None:
        self._client.close()
