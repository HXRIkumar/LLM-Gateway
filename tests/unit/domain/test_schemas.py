"""Unit tests for the canonical OpenAI-compatible schemas.

Parsing and re-serialization are checked against real OpenAI request/response/
chunk payloads (``tests/fixtures/openai``) to guard the compatibility contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "openai"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_request_parses_real_openai_fixture() -> None:
    payload = _load("chat_request.json")
    req = ChatCompletionRequest.model_validate(payload)
    assert req.model == "gpt-4o-mini"
    assert len(req.messages) == 2
    assert req.messages[0].role == "system"
    assert req.messages[1].content == "Hello!"
    assert req.temperature == 0.7
    assert req.max_tokens == 100
    assert req.stream is False


def test_request_preserves_unknown_fields_for_forward_compat() -> None:
    payload = _load("chat_request.json")
    payload["some_future_openai_param"] = {"nested": True}
    req = ChatCompletionRequest.model_validate(payload)
    dumped = req.model_dump()
    assert dumped["some_future_openai_param"] == {"nested": True}


def test_request_rejects_missing_model() -> None:
    with pytest.raises(PydanticValidationError):
        ChatCompletionRequest.model_validate({"messages": [{"role": "user", "content": "hi"}]})


def test_request_rejects_empty_messages() -> None:
    with pytest.raises(PydanticValidationError):
        ChatCompletionRequest.model_validate({"model": "gpt-4o-mini", "messages": []})


def test_request_rejects_wrong_type() -> None:
    with pytest.raises(PydanticValidationError):
        ChatCompletionRequest.model_validate(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": "hot",
            }
        )


def test_response_round_trips_real_openai_fixture() -> None:
    payload = _load("chat_response.json")
    resp = ChatCompletionResponse.model_validate(payload)
    assert resp.id == "chatcmpl-123"
    assert resp.object == "chat.completion"
    assert resp.model == "gpt-4o-mini"
    assert resp.system_fingerprint == "fp_44709d6fcb"
    assert resp.choices[0].message.role == "assistant"
    assert resp.choices[0].message.content == "Hello there, how may I assist you today?"
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage is not None
    assert resp.usage.total_tokens == 21

    # Re-serialized, the structural fields survive intact.
    dumped = resp.model_dump(exclude_none=True)
    assert dumped["object"] == "chat.completion"
    assert dumped["choices"][0]["message"]["content"].startswith("Hello there")
    assert dumped["usage"]["total_tokens"] == 21


def test_chunk_parses_real_openai_fixture() -> None:
    payload = _load("chat_chunk.json")
    chunk = ChatCompletionChunk.model_validate(payload)
    assert chunk.object == "chat.completion.chunk"
    assert chunk.choices[0].delta.content == "Hello"
    assert chunk.choices[0].delta.role == "assistant"
    assert chunk.choices[0].finish_reason is None


def test_response_object_discriminator_is_fixed() -> None:
    # The object literal must be exactly "chat.completion" — a client depends on it.
    with pytest.raises(PydanticValidationError):
        ChatCompletionResponse.model_validate(
            {
                "id": "x",
                "object": "not.a.completion",
                "created": 1,
                "model": "m",
                "choices": [],
            }
        )
