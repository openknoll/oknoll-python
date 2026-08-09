"""Local Ollama provider behind the okf-core ModelProvider seam.

Talks to Ollama's REST API (``POST /api/chat``) over httpx with an injectable
transport, so tests run fully offline. The model is folded into ``id``
(``ollama:<model>``) for the same cache-key reason as the Anthropic provider.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from oknoll_providers.errors import ProviderError
from oknoll_providers.prompts import render

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
_DEFAULT_TIMEOUT_SECONDS = 300.0


def _normalize_host(host: str) -> str:
    host = host.strip().rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    return host


def _display_host(host: str) -> str:
    """The host with any userinfo credentials removed — safe for error messages."""
    scheme, sep, rest = host.partition("://")
    authority, slash, tail = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return scheme + sep + authority + slash + tail


def ping_ollama(
    host: str | None = None,
    *,
    timeout: float = 2.0,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Probe ``GET /api/version``; returns the server version or raises ProviderError.

    Used by `oknoll doctor` — a cheap liveness check that never loads a model.
    """
    resolved = _normalize_host(host or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST)
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.get(f"{resolved}/api/version")
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"cannot reach Ollama at {_display_host(resolved)} — is `ollama serve` running?"
        ) from exc
    if response.status_code != 200:
        raise ProviderError(
            f"Ollama returned HTTP {response.status_code} from "
            f"{_display_host(resolved)}/api/version"
        )
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderError("Ollama returned a response that is not valid JSON") from exc
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else "unknown"


class OllamaProvider:
    """Bounded text completion via a local Ollama server."""

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

    def complete(self, prompt_id: str, payload: dict[str, Any]) -> str:
        system, user = render(prompt_id, payload)
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        try:
            response = self._client.post(f"{self._host}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"cannot reach Ollama at {_display_host(self._host)} — is `ollama serve` running?"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc

        if response.status_code != 200:
            snippet = response.text.strip()[:200]
            raise ProviderError(
                f"Ollama returned HTTP {response.status_code} for model {self._model!r}"
                f"{f': {snippet}' if snippet else ''} — "
                f"if the model is missing, run `ollama pull {self._model}`"
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("Ollama returned a response that is not valid JSON") from exc

        if not isinstance(data, dict):
            raise ProviderError("Ollama returned an unexpected response shape")
        # A completion that did not finish cleanly must never enter the build
        # cache or an immutable revision (`done_reason: "length"` = truncation).
        done_reason = data.get("done_reason")
        if data.get("done") is False or done_reason not in (None, "stop"):
            raise ProviderError(
                f"the Ollama completion did not finish cleanly "
                f"(done_reason: {done_reason!r}) — truncated output is never used; "
                "raise the model's num_predict/context settings"
            )
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        text = str(content).strip() if isinstance(content, str) else ""
        if not text:
            raise ProviderError(f"Ollama returned an empty completion for model {self._model!r}")
        return text

    def close(self) -> None:
        self._client.close()
