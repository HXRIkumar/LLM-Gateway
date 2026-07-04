"""Compatibility gate — drive Conduit with the real OpenAI SDK.

The stock ``openai`` SDK talks to the Conduit ASGI app in-process (its httpx
client uses an ASGITransport, which respx does not touch), while respx mocks only
the *upstream* providers. This proves the Phase 1 promise: a stock OpenAI client
works unmodified against both OpenAI and Ollama, unary and streaming, by changing
only ``model`` — no network, no real keys.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openai import AsyncOpenAI

from conduit.config import Settings
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = [pytest.mark.integration, pytest.mark.compat]

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"

OPENAI_RESPONSE = {
    "id": "chatcmpl-openai",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hi from openai"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

OLLAMA_RESPONSE = {
    "model": "llama3.2",
    "created_at": "2024-01-01T00:00:00Z",
    "message": {"role": "assistant", "content": "hi from ollama"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 3,
    "eval_count": 2,
}

OPENAI_SSE = (
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

OLLAMA_NDJSON = (
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":"Hello"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":" world"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop"}\n'
)


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def _sdk_client(app: FastAPI, key: str) -> AsyncOpenAI:
    # The SDK's transport points into the Conduit ASGI app; respx leaves it alone.
    sdk_http = AsyncClient(transport=ASGITransport(app=app), base_url="http://conduit")
    return AsyncOpenAI(base_url="http://conduit/v1", api_key=key, http_client=sdk_http)


async def test_openai_sdk_unary_across_providers(postgres_url: str, redis_url: str) -> None:
    settings = Settings(database_url=postgres_url, redis_url=redis_url, openai_api_key="sk-test")  # type: ignore[arg-type]
    app = create_app(settings)
    async with lifespan(app):
        client = await _sdk_client(app, await _mint_key(app))
        with respx.mock:
            respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
            respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))

            openai_completion = await client.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]
            )
            assert openai_completion.choices[0].message.content == "hi from openai"
            assert openai_completion.choices[0].finish_reason == "stop"

            # Change only the model → Ollama, same SDK, same call shape.
            ollama_completion = await client.chat.completions.create(
                model="llama3.2", messages=[{"role": "user", "content": "hi"}]
            )
            assert ollama_completion.choices[0].message.content == "hi from ollama"
        await client.close()


async def test_openai_sdk_streaming_across_providers(postgres_url: str, redis_url: str) -> None:
    settings = Settings(database_url=postgres_url, redis_url=redis_url, openai_api_key="sk-test")  # type: ignore[arg-type]
    app = create_app(settings)
    async with lifespan(app):
        client = await _sdk_client(app, await _mint_key(app))
        with respx.mock:
            respx.post(OPENAI_URL).mock(
                return_value=httpx.Response(
                    200, text=OPENAI_SSE, headers={"content-type": "text/event-stream"}
                )
            )
            respx.post(OLLAMA_URL).mock(
                return_value=httpx.Response(
                    200, text=OLLAMA_NDJSON, headers={"content-type": "application/x-ndjson"}
                )
            )

            for model in ("gpt-4o-mini", "llama3.2"):
                stream = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    stream=True,
                )
                parts = [
                    chunk.choices[0].delta.content
                    async for chunk in stream
                    if chunk.choices and chunk.choices[0].delta.content
                ]
                assert "".join(parts) == "Hello world"
        await client.close()


async def test_openai_sdk_models_list(postgres_url: str, redis_url: str) -> None:
    settings = Settings(database_url=postgres_url, redis_url=redis_url)
    app = create_app(settings)
    async with lifespan(app):
        client = await _sdk_client(app, await _mint_key(app))
        listed = await client.models.list()
        ids = {model.id for model in listed.data}
        assert {"gpt-4o-mini", "llama3.2"} <= ids
        await client.close()
