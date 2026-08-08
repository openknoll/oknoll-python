"""Provider spec resolution shared by the CLI and (later) the eval harness.

The spec grammar extends okf-core's ``resolve_provider`` (which stays
stub-only, since okf-core may never reach the network):

- ``stub``               → okf-core's deterministic CI provider
- ``anthropic``          → Anthropic API, default model
- ``anthropic:<model>``  → Anthropic API, explicit model
- ``ollama:<model>``     → local Ollama server (model is required)
"""

from __future__ import annotations

from okf_core import EmbeddingProvider, ModelProvider, resolve_provider
from okf_core import resolve_embedder as _resolve_stub_embedder

from oknoll_providers.anthropic_provider import DEFAULT_ANTHROPIC_MODEL, AnthropicProvider
from oknoll_providers.ollama_embedder import OllamaEmbedder
from oknoll_providers.ollama_provider import OllamaProvider

_AVAILABLE = "'stub', 'anthropic[:<model>]', 'ollama:<model>'"
_AVAILABLE_EMBEDDERS = "'stub', 'ollama:<model>' (Anthropic has no embeddings API)"


def resolve(spec: str) -> ModelProvider:
    """Resolve a ``[build].model`` / ``--model`` spec to a provider instance."""
    spec = spec.strip()
    if spec == "stub":
        return resolve_provider("stub")
    scheme, separator, model = spec.partition(":")
    model = model.strip()
    if scheme == "anthropic":
        if separator and not model:
            raise ValueError(
                "empty model after 'anthropic:' — use 'anthropic' for the default "
                f"({DEFAULT_ANTHROPIC_MODEL}) or 'anthropic:<model>'"
            )
        return AnthropicProvider(model or DEFAULT_ANTHROPIC_MODEL)
    if scheme == "ollama":
        if not model:
            raise ValueError(
                "the ollama provider needs an explicit model — use 'ollama:<model>', "
                "e.g. 'ollama:llama3'"
            )
        return OllamaProvider(model)
    raise ValueError(f"unknown model provider {spec!r} — available: {_AVAILABLE}")


def resolve_embedder(spec: str) -> EmbeddingProvider:
    """Resolve a ``[rag].embedder`` / ``--embedder`` spec to an embedding provider."""
    spec = spec.strip()
    if spec == "stub":
        return _resolve_stub_embedder("stub")
    scheme, _, model = spec.partition(":")
    model = model.strip()
    if scheme == "ollama":
        if not model:
            raise ValueError(
                "the ollama embedder needs an explicit model — use 'ollama:<model>', "
                "e.g. 'ollama:nomic-embed-text'"
            )
        return OllamaEmbedder(model)
    raise ValueError(f"unknown embedding provider {spec!r} — available: {_AVAILABLE_EMBEDDERS}")
