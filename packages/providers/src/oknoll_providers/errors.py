"""Provider failures that a CLI command turns into a one-line exit, never a traceback."""

from __future__ import annotations


class ProviderError(RuntimeError):
    """A real model provider could not be constructed or could not complete a call."""
