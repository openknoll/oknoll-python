"""Spec grammar for `[build].model` / `ask --model` — stub delegation and provider ids."""

from __future__ import annotations

import pytest
from oknoll_providers import ProviderError, resolve


def test_stub_delegates_to_okf_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert resolve("stub").id == "stub"


def test_anthropic_spec_folds_model_into_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert resolve("anthropic:claude-opus-5").id == "anthropic:claude-opus-5"


def test_bare_anthropic_uses_the_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert resolve("anthropic").id == "anthropic:claude-opus-5"


def test_anthropic_without_key_fails_with_the_env_var_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        resolve("anthropic:claude-opus-5")


def test_ollama_spec_folds_model_into_id() -> None:
    assert resolve("ollama:llama3").id == "ollama:llama3"


def test_ollama_requires_an_explicit_model() -> None:
    with pytest.raises(ValueError, match="ollama:<model>"):
        resolve("ollama")


def test_unknown_spec_matches_the_okf_core_error_shape() -> None:
    with pytest.raises(ValueError, match="unknown model provider"):
        resolve("gpt-42")


def test_trailing_colon_with_empty_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty model after 'anthropic:'"):
        resolve("anthropic:")


def test_specs_are_whitespace_tolerant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert resolve("  anthropic:claude-opus-5  ").id == "anthropic:claude-opus-5"
    assert resolve("ollama: llama3").id == "ollama:llama3"
