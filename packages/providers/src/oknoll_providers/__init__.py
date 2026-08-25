"""Real model providers (Anthropic API, Ollama) behind the okf-core ModelProvider seam.

okf-core is structurally barred from network access (a `make security` release
gate scans it for network imports), so every provider that makes HTTP calls
lives here. CI never constructs these — the deterministic stub remains the
default everywhere.
"""

from oknoll_providers.anthropic_provider import DEFAULT_ANTHROPIC_MODEL, AnthropicProvider
from oknoll_providers.env import load_env
from oknoll_providers.errors import ProviderError
from oknoll_providers.ollama_embedder import OllamaEmbedder
from oknoll_providers.ollama_provider import DEFAULT_OLLAMA_HOST, OllamaProvider, ping_ollama
from oknoll_providers.registry import resolve, resolve_embedder

__version__ = "0.4.0a1"

__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_OLLAMA_HOST",
    "AnthropicProvider",
    "OllamaEmbedder",
    "OllamaProvider",
    "ProviderError",
    "__version__",
    "load_env",
    "ping_ollama",
    "resolve",
    "resolve_embedder",
]
