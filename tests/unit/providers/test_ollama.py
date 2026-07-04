"""respx-mocked tests for the Ollama adapter.

Mirror the OpenAI adapter coverage against Ollama's very different API shape, and
prove the same canonical request/response works unchanged across both backends.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from conduit.domain.errors import (
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    UpstreamInvalidRequest,
)
from conduit.domain.schemas import ChatCompletionRequest, Message
from conduit.providers.ollama import OllamaProvider
from conduit.providers.openai import OpenAIProvider

OLLAMA_BASE = "http://localhost:11434"
CHAT_URL = f"{OLLAMA_BASE}/api/chat"
OPENAI_BASE = "https://api.openai.com/v1"

OLLAMA_RESPONSE = {
    "model": "llama3.2",
    "created_at": "2024-01-01T00:00:02Z",
    "message": {"role": "assistant", "content": "The sky is blue due to Rayleigh scattering."},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 26,
    "eval_count": 12,
}

OLLAMA_STREAM = (
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":"Hello"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":" world"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":""},'
    '"done":true,"done_reason":"stop","prompt_eval_count":10,"eval_count":5}\n'
)


def _request(*, stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="llama3.2",
        messages=[Message(role="user", content="why is the sky blue?")],
        temperature=0.5,
        max_tokens=64,
        stream=stream,
    )


@pytest.fixture
async def provider() -> AsyncIterator[OllamaProvider]:
    async with httpx.AsyncClient() as client:
        yield OllamaProvider(client, base_url=OLLAMA_BASE)


async def test_chat_completion_translates_response(provider: OllamaProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))
        resp = await provider.chat_completion(_request())

    assert resp.object == "chat.completion"
    assert resp.id.startswith("chatcmpl-")
    assert resp.model == "llama3.2"
    assert resp.choices[0].message.content == "The sky is blue due to Rayleigh scattering."
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 26
    assert resp.usage.completion_tokens == 12
    assert resp.usage.total_tokens == 38


async def test_request_translated_to_ollama_shape(provider: OllamaProvider) -> None:
    with respx.mock:
        route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))
        await provider.chat_completion(_request())

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "llama3.2"
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "why is the sky blue?"}]
    # OpenAI-style params are moved under Ollama's `options` block
    assert body["options"]["temperature"] == 0.5
    assert body["options"]["num_predict"] == 64


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, UpstreamInvalidRequest),
        (400, UpstreamInvalidRequest),
        (429, ProviderRateLimited),
        (500, ProviderError),
    ],
)
async def test_error_status_maps_to_domain_error(
    provider: OllamaProvider, status: int, expected: type[Exception]
) -> None:
    # Ollama's error body is a flat {"error": "..."} string, not OpenAI's nested shape.
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(status, json={"error": "model 'x' not found"})
        )
        with pytest.raises(expected) as exc_info:
            await provider.chat_completion(_request())
    assert "not found" in str(exc_info.value)


async def test_timeout_maps_to_provider_timeout(provider: OllamaProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(ProviderTimeout):
            await provider.chat_completion(_request())


async def test_streaming_translates_ndjson_to_canonical_chunks(provider: OllamaProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, text=OLLAMA_STREAM, headers={"content-type": "application/x-ndjson"}
            )
        )
        chunks = [chunk async for chunk in provider.stream_chat_completion(_request(stream=True))]

    assert len(chunks) == 3
    assert chunks[0].choices[0].delta.role == "assistant"
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text == "Hello world"
    assert chunks[-1].choices[0].finish_reason == "stop"
    # token usage arrives on the final chunk
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 15


async def test_health_probes_tags_endpoint(provider: OllamaProvider) -> None:
    with respx.mock:
        respx.get(f"{OLLAMA_BASE}/api/tags").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        status = await provider.health()
    assert status.healthy is True


async def test_same_canonical_request_works_across_both_providers() -> None:
    request = ChatCompletionRequest(
        model="test-model",
        messages=[Message(role="user", content="Hello!")],
    )
    openai_response = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi from openai"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    async with httpx.AsyncClient() as client:
        openai_provider = OpenAIProvider(client, api_key="sk-test", base_url=OPENAI_BASE)
        ollama_provider = OllamaProvider(client, base_url=OLLAMA_BASE)
        with respx.mock:
            respx.post(f"{OPENAI_BASE}/chat/completions").mock(
                return_value=httpx.Response(200, json=openai_response)
            )
            respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))
            from_openai = await openai_provider.chat_completion(request)
            from_ollama = await ollama_provider.chat_completion(request)

    # Identical canonical output shape from two very different backends.
    assert from_openai.object == from_ollama.object == "chat.completion"
    assert from_openai.choices[0].message.content == "hi from openai"
    assert from_ollama.choices[0].message.content.startswith("The sky is blue")
    assert from_openai.usage is not None and from_ollama.usage is not None
