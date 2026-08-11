"""Anthropic API provider behind the okf-core ModelProvider seam.

The model is folded into ``id`` (``anthropic:<model>``) because
``generation_cache_key`` caches model-generated fields by model ID, and the
ask trace records ``provider.id`` as the model.

The ``anthropic`` SDK is imported lazily so that merely importing the CLI
(``oknoll --help``) never pays for it; only constructing a real provider does.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

import httpx

from oknoll_providers.errors import ProviderError
from oknoll_providers.prompts import render

if TYPE_CHECKING:
    import anthropic

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
# On current Claude models (Opus 5+) adaptive thinking is on by default and
# max_tokens caps thinking + response text *together* — a cap sized for the
# short visible answer alone truncates once the model spends its thinking
# budget, and the truncation discipline below then fails the build.
_DEFAULT_MAX_TOKENS = 16000

# Server-side refusal fallback: when a safety classifier declines a request
# (stop_reason "refusal"), the API re-runs it on Anthropic's recommended
# fallback model inside the same call instead of returning the refusal.
# `served_model_id` records which model actually answered, and the pipeline
# stores it with each cached generation — the cache *key* stays the requested
# model (the request's identity), the cached *value* carries the provenance.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    """Bounded text completion via the Anthropic Messages API."""

    id: str

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        http_client: httpx.Client | None = None,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set — add it to the project's .env file "
                "or export it in the environment"
            )
        import anthropic as anthropic_sdk

        self.id = f"anthropic:{model}"
        self.served_model_id = self.id
        self._model = model
        self._max_tokens = max_tokens
        self._client: anthropic.Anthropic = anthropic_sdk.Anthropic(
            api_key=key, http_client=http_client
        )

    def complete(self, prompt_id: str, payload: dict[str, Any]) -> str:
        import anthropic as anthropic_sdk

        system, user = render(prompt_id, payload)
        self.served_model_id = self.id
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                betas=[_FALLBACK_BETA],
                extra_body={"fallbacks": "default"},
            )
        except anthropic_sdk.AuthenticationError as exc:
            raise ProviderError(
                "the Anthropic API rejected the key (401) — check ANTHROPIC_API_KEY"
            ) from exc
        except anthropic_sdk.RateLimitError as exc:
            raise ProviderError(
                "the Anthropic API rate limit was hit (429) — retry in a moment"
            ) from exc
        except anthropic_sdk.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error ({exc.status_code}): {exc.message}") from exc
        except anthropic_sdk.APIConnectionError as exc:
            raise ProviderError(f"cannot reach the Anthropic API: {exc}") from exc
        except anthropic_sdk.APIError as exc:
            # e.g. APIResponseValidationError — subclasses APIError directly,
            # not APIStatusError, and must not escape as a traceback.
            raise ProviderError(f"Anthropic API error: {exc}") from exc

        served = str(getattr(response, "model", "") or self._model)
        self.served_model_id = f"anthropic:{served}"
        if served != self._model:
            print(
                f"note: {prompt_id} was declined by {self._model} and served by "
                f"the fallback model {served}",
                file=sys.stderr,
            )

        if response.stop_reason == "refusal":
            # The whole fallback chain declined.
            category = _refusal_category(response)
            detail = f" (category: {category})" if category else ""
            raise ProviderError(
                f"the model declined this request{detail} — the answer was not generated"
            )
        if response.stop_reason == "max_tokens":
            # A truncated completion must never be cached into an immutable revision.
            raise ProviderError(
                f"the completion was truncated at {self._max_tokens} tokens — "
                "raise max_tokens for this provider"
            )
        if response.stop_reason not in ("end_turn", "stop_sequence"):
            # pause_turn, model_context_window_exceeded, tool_use, ... — anything
            # that is not a clean finish is partial output and is never used.
            raise ProviderError(
                f"the completion did not finish cleanly "
                f"(stop reason: {response.stop_reason!r}) — partial output is never used"
            )

        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise ProviderError("the Anthropic API returned an empty completion")
        return text

    def close(self) -> None:
        self._client.close()


def _refusal_category(response: object) -> str | None:
    details = getattr(response, "stop_details", None)
    if details is None:
        return None
    if isinstance(details, dict):
        category = details.get("category")
    else:
        category = getattr(details, "category", None)
    return str(category) if category else None
