"""Unit tests for pure request classification."""

from __future__ import annotations

from conduit.domain.routing.classify import classify
from conduit.domain.schemas import ChatCompletionRequest


def _req(**kwargs: object) -> ChatCompletionRequest:
    base: dict[str, object] = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
    }
    base.update(kwargs)
    return ChatCompletionRequest.model_validate(base)


def test_plain_chat_has_no_special_requirements() -> None:
    req = classify(_req())
    assert req.needs_tools is False
    assert req.needs_vision is False
    assert req.needs_json_mode is False
    assert req.min_context >= 1


def test_tools_are_detected() -> None:
    req = classify(_req(tools=[{"type": "function", "function": {"name": "f"}}]))
    assert req.needs_tools is True


def test_vision_detected_from_image_parts() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        }
    ]
    assert classify(_req(messages=messages)).needs_vision is True


def test_json_mode_detected() -> None:
    assert classify(_req(response_format={"type": "json_object"})).needs_json_mode is True


def test_long_context_raises_min_context() -> None:
    long_text = "word " * 10_000  # ~50k chars → ~12k tokens
    req = classify(_req(messages=[{"role": "user", "content": long_text}], max_tokens=1_000))
    assert req.min_context > 10_000  # prompt estimate + completion headroom


def test_completion_headroom_counts_toward_context() -> None:
    small = classify(_req(max_tokens=None))
    large = classify(_req(max_tokens=4_000))
    assert large.min_context - small.min_context == 4_000
