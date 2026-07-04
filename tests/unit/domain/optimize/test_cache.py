"""Unit tests for the pure cache policy: cacheability, key stability, transforms."""

from __future__ import annotations

from conduit.domain.optimize.cache import (
    assemble_response,
    cache_key,
    is_cacheable,
    response_to_chunks,
)
from conduit.domain.schemas import ChatCompletionRequest, Message


def _req(**overrides: object) -> ChatCompletionRequest:
    base: dict = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.0,
    }
    base.update(overrides)
    return ChatCompletionRequest.model_validate(base)


def test_deterministic_request_is_cacheable() -> None:
    assert is_cacheable(_req()) is True


def test_nonzero_temperature_not_cacheable() -> None:
    assert is_cacheable(_req(temperature=0.7)) is False
    assert is_cacheable(_req(temperature=None)) is False


def test_tools_vision_multi_choice_not_cacheable() -> None:
    assert is_cacheable(_req(tools=[{"type": "function"}])) is False
    assert is_cacheable(_req(tool_choice="auto")) is False
    assert is_cacheable(_req(n=2)) is False
    vision = _req(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}])
    assert is_cacheable(vision) is False


def test_cache_key_is_stable_and_ignores_stream_and_user() -> None:
    a = _req(stream=False, user="alice")
    b = _req(stream=True, user="bob")
    assert cache_key(a) == cache_key(b)


def test_cache_key_changes_with_content_and_params() -> None:
    assert cache_key(_req()) != cache_key(_req(messages=[Message(role="user", content="bye")]))
    assert cache_key(_req()) != cache_key(_req(max_tokens=100))


def test_response_chunks_roundtrip_reassembles_content() -> None:
    original = assemble_response(
        id="x", created=1, model="m", content="hello world", finish_reason="stop", usage=None
    )
    chunks = response_to_chunks(original)
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text == "hello world"
    assert chunks[-1].choices[0].finish_reason == "stop"
