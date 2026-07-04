"""respx-mocked tests for the OpenAI adapter: success, error mapping, streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from conduit.domain.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    UpstreamInvalidRequest,
)
from conduit.domain.schemas import ChatCompletionRequest, Message
from conduit.providers.openai import OpenAIProvider

BASE = "https://api.openai.com/v1"
COMPLETIONS = f"{BASE}/chat/completions"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "openai"

SSE_BODY = (
    'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
    '"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
    'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
    '"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}\n\n'
    'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
    '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def _response_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "chat_response.json").read_text())


def _request(*, stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="gpt-4o-mini",
        messages=[Message(role="user", content="Hello!")],
        stream=stream,
    )


@pytest.fixture
async def provider() -> AsyncIterator[OpenAIProvider]:
    async with httpx.AsyncClient() as client:
        yield OpenAIProvider(client, api_key="sk-test", base_url=BASE)


async def test_chat_completion_success(provider: OpenAIProvider) -> None:
    with respx.mock:
        route = respx.post(COMPLETIONS).mock(
            return_value=httpx.Response(200, json=_response_fixture())
        )
        resp = await provider.chat_completion(_request())

    assert route.called
    assert resp.object == "chat.completion"
    assert resp.choices[0].message.content == "Hello there, how may I assist you today?"
    assert resp.usage is not None and resp.usage.total_tokens == 21

    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer sk-test"
    body = json.loads(sent.content)
    assert body["model"] == "gpt-4o-mini"
    assert body["stream"] is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, UpstreamInvalidRequest),
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderRateLimited),
        (500, ProviderError),
        (503, ProviderError),
    ],
)
async def test_error_status_maps_to_domain_error(
    provider: OpenAIProvider, status: int, expected: type[Exception]
) -> None:
    with respx.mock:
        respx.post(COMPLETIONS).mock(
            return_value=httpx.Response(
                status, json={"error": {"message": "boom", "type": "invalid_request_error"}}
            )
        )
        with pytest.raises(expected) as exc_info:
            await provider.chat_completion(_request())
    assert "boom" in str(exc_info.value)


async def test_timeout_maps_to_provider_timeout(provider: OpenAIProvider) -> None:
    with respx.mock:
        respx.post(COMPLETIONS).mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(ProviderTimeout):
            await provider.chat_completion(_request())


async def test_connection_error_maps_to_provider_error(provider: OpenAIProvider) -> None:
    with respx.mock:
        respx.post(COMPLETIONS).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(ProviderError):
            await provider.chat_completion(_request())


async def test_streaming_yields_canonical_chunks(provider: OpenAIProvider) -> None:
    with respx.mock:
        route = respx.post(COMPLETIONS).mock(
            return_value=httpx.Response(
                200, text=SSE_BODY, headers={"content-type": "text/event-stream"}
            )
        )
        chunks = [chunk async for chunk in provider.stream_chat_completion(_request(stream=True))]

    # request asked for a stream
    assert json.loads(route.calls.last.request.content)["stream"] is True
    # [DONE] is consumed, not yielded
    assert len(chunks) == 4
    assert chunks[0].choices[0].delta.role == "assistant"
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text == "Hello world"
    assert chunks[-1].choices[0].finish_reason == "stop"


async def test_streaming_error_status_maps_before_iteration(provider: OpenAIProvider) -> None:
    with respx.mock:
        respx.post(COMPLETIONS).mock(
            return_value=httpx.Response(429, json={"error": {"message": "slow down"}})
        )
        with pytest.raises(ProviderRateLimited):
            async for _ in provider.stream_chat_completion(_request(stream=True)):
                pass


async def test_health_reports_healthy_on_2xx(provider: OpenAIProvider) -> None:
    with respx.mock:
        respx.get(f"{BASE}/models").mock(return_value=httpx.Response(200, json={"data": []}))
        status = await provider.health()
    assert status.healthy is True


async def test_health_reports_unhealthy_on_5xx(provider: OpenAIProvider) -> None:
    with respx.mock:
        respx.get(f"{BASE}/models").mock(return_value=httpx.Response(500))
        status = await provider.health()
    assert status.healthy is False
