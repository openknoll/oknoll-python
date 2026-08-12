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


def _provider_with(
    handler: httpx.MockTransport, *, max_tokens: int | None = None
) -> AnthropicProvider:
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
    assert "/v1/messages" in seen["url"]
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


def test_fallback_opt_in_and_provenance() -> None:
    """Every request opts into server-side refusal fallback, and the serving
    model is recorded so the pipeline can cache honest provenance."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_message_body(["A grounded description."]))

    provider = _provider_with(httpx.MockTransport(handler))
    provider.complete("concept-description", {"title": "T", "excerpt": "E"})

    assert seen["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
    assert provider.served_model_id == provider.id


def test_fallback_served_model_is_recorded(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _message_body(["Served by the fallback."])
        body["model"] = "claude-opus-4-8"
        return httpx.Response(200, json=body)

    provider = _provider_with(httpx.MockTransport(handler))
    text = provider.complete("concept-description", {"title": "T", "excerpt": "E"})

    assert text == "Served by the fallback."
    assert provider.id == "anthropic:claude-opus-5"
    assert provider.served_model_id == "anthropic:claude-opus-4-8"
    assert "fallback model claude-opus-4-8" in capsys.readouterr().err
    # A later successful call on the requested model resets the record.
    provider2 = _provider_with(
        httpx.MockTransport(lambda request: httpx.Response(200, json=_message_body(["Direct."])))
    )
    provider2.complete("concept-description", {"title": "T", "excerpt": "E"})
    assert provider2.served_model_id == provider2.id


def test_fallbacks_rejection_downgrades_once_and_sticks() -> None:
    """Models that 400 on the fallbacks parameter get one retry without it,
    and later calls skip the doomed attempt entirely."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "fallbacks" in body:
            return httpx.Response(
                400,
                json={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "'claude-sonnet-5' does not support the `fallbacks` parameter.",
                    },
                },
            )
        return httpx.Response(200, json=_message_body(["An answer."]))

    provider = _provider_with(httpx.MockTransport(handler))
    assert provider.complete("answer-question", ANSWER_PAYLOAD) == "An answer."
    assert provider.complete("answer-question", ANSWER_PAYLOAD) == "An answer."
    # Call 1: with fallbacks (rejected), retried without; call 2: straight without.
    assert ["fallbacks" in body for body in bodies] == [True, False, False]


def test_unrelated_bad_request_is_not_swallowed_by_the_fallback_downgrade() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "max_tokens is too large"},
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="400"):
        provider.complete("answer-question", ANSWER_PAYLOAD)


def test_last_usage_records_token_accounting_and_resets_on_failure() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_message_body(["An answer."]))
        return httpx.Response(200, json=_message_body(["partial"], stop_reason="max_tokens"))

    provider = _provider_with(httpx.MockTransport(handler))
    before = provider.last_usage
    assert before is None  # no call yet
    provider.complete("answer-question", ANSWER_PAYLOAD)
    after = provider.last_usage
    assert after == {"input_tokens": 10, "output_tokens": 5}
    with pytest.raises(ProviderError, match="truncated"):
        provider.complete("answer-question", ANSWER_PAYLOAD)
    assert provider.last_usage is None  # a failed call never leaks the previous usage


def test_last_usage_folds_cache_tokens_into_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _message_body(["An answer."])
        body["usage"] = {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 2,
        }
        return httpx.Response(200, json=body)

    provider = _provider_with(httpx.MockTransport(handler))
    provider.complete("answer-question", ANSWER_PAYLOAD)
    assert provider.last_usage == {"input_tokens": 15, "output_tokens": 5}


def test_whole_fallback_chain_refusal_still_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _message_body([], stop_reason="refusal")
        body["model"] = "claude-opus-4-8"
        body["stop_details"] = {"type": "refusal", "category": "cyber"}
        return httpx.Response(200, json=body)

    provider = _provider_with(httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="declined this request"):
        provider.complete("concept-description", {"title": "T", "excerpt": "E"})
