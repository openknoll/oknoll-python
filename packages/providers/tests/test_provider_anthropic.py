"""AnthropicProvider over httpx.MockTransport — fully offline, no SDK network access."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from oknoll_providers import AnthropicProvider, ProviderError

ANSWER_PAYLOAD = {
    "question": "Who signs off a release?",
    "evidence": [
        {"path": "concepts/duty-roster.md", "title": "Duty roster", "excerpt": "The lead signs."}
    ],
}


def _message_body(
    text_blocks: list[str],
    stop_reason: str = "end_turn",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text} for text in text_blocks],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    body.update(extra or {})
    return body


def _provider_with(handler: httpx.MockTransport, *, max_tokens: int | None = None) -> AnthropicProvider:
    kwargs: dict[str, Any] = {} if max_tokens is None else {"max_tokens": max_tokens}
    return AnthropicProvider(
        "claude-opus-5",
        api_key="test-key",
        http_client=httpx.Client(transport=handler),
        **kwargs,
    )


def test_request_shape_and_headers() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_message_body(["An answer. [concepts/duty-roster.md]"]))

    provider = _provider_with(httpx.MockTransport(handler))
    text = provider.complete("answer-question", ANSWER_PAYLOAD)

    assert text == "An answer. [concepts/duty-roster.md]"
    assert seen["url"].endswith("/v1/messages")
    assert seen["headers"]["x-api-key"] == "test-key"
    assert "anthropic-version" in seen["headers"]
    body = seen["body"]
    assert body["model"] == "claude-opus-5"
    # Thinking is on by default on Opus 5 and counts against max_tokens, so
    # the default cap must leave headroom well beyond the visible answer.
    assert body["max_tokens"] == 16000
    assert body["messages"][0]["role"] == "user"
    assert "Who signs off a release?" in body["messages"][0]["content"]
    assert "concepts/duty-roster.md" in body["messages"][0]["content"]
    assert "evidence passages" in body["system"]
    # Removed on modern models — sending any of these would 400.
    for forbidden in ("temperature", "top_p", "top_k", "thinking"):
        assert forbidden not in body


def test_multiple_text_blocks_are_joined() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_message_body(["First. ", "Second."]))

    provider = _provider_with(httpx.MockTransport(handler))
    assert provider.complete("answer-question", ANSWER_PAYLOAD) == "First. Second."


def test_refusal_stop_reason_raises_with_category() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _message_body(
            [],
            stop_reason="refusal",
            extra={"stop_details": {"type": "refusal", "category": "cyber"}},
        )
        return httpx.Response(200, json=body)

    provider = _provider_with(httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match=r"declined.*cyber"):
        provider.complete("answer-question", ANSWER_PAYLOAD)


def test_truncated_completion_raises_instead_of_returning_partial_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_message_body(["partial"], stop_reason="max_tokens"))

    provider = _provider_with(httpx.MockTransport(handler), max_tokens=64)
    with pytest.raises(ProviderError, match="truncated"):
        provider.complete("answer-question", ANSWER_PAYLOAD)


def test_authentication_error_names_the_env_var() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        provider.complete("answer-question", ANSWER_PAYLOAD)


def test_other_api_errors_carry_the_status_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "bad request"},
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="400"):
        provider.complete("answer-question", ANSWER_PAYLOAD)


def test_missing_key_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider("claude-opus-5")


def test_unknown_prompt_id_raises_value_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        return httpx.Response(200, json=_message_body(["x"]))

    provider = _provider_with(httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="no prompt"):
        provider.complete("nonsense", {})


def test_unexpected_stop_reason_never_returns_partial_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_message_body(["partial"], stop_reason="pause_turn"))

    provider = _provider_with(httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="did not finish cleanly"):
        provider.complete("answer-question", ANSWER_PAYLOAD)


def test_unparseable_success_body_maps_to_provider_error() -> None:
    """APIResponseValidationError subclasses APIError directly, not APIStatusError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = _provider_with(httpx.MockTransport(handler))
    with pytest.raises((ProviderError, ValueError)) as excinfo:
        provider.complete("answer-question", ANSWER_PAYLOAD)
    assert isinstance(excinfo.value, ProviderError)


def test_close_releases_the_http_client() -> None:
    provider = _provider_with(httpx.MockTransport(lambda r: httpx.Response(200)))
    provider.close()
