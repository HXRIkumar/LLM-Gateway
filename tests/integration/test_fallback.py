"""Integration tests for automatic fallback across providers (respx + real Redis)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conduit.config import Settings
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"
DOWN = httpx.Response(503, json={"error": {"message": "down"}})

OLLAMA_UNARY = {
    "model": "llama3.2",
    "created_at": "t",
    "message": {"role": "assistant", "content": "from ollama fallback"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 3,
    "eval_count": 4,
}
OLLAMA_STREAM = (
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":"Hello"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"content":" fallback"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"content":""},"done":true,"done_reason":"stop"}\n'
)


def _app(postgres_url: str, redis_url: str) -> FastAPI:
    return create_app(
        Settings(  # type: ignore[call-arg]
            database_url=postgres_url,
            redis_url=redis_url,
            openai_api_key="sk-test",
            rate_limit_enabled=False,
            retry_max_attempts=1,
            model_fallbacks={"gpt-4o-mini": ["llama3.2"]},
        )
    )


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def _client_key(app: FastAPI) -> tuple[AsyncClient, dict[str, str]]:
    key = await _mint_key(app)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    return client, {"Authorization": f"Bearer {key}"}


async def test_unary_falls_back_to_next_provider(postgres_url: str, redis_url: str) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        client, headers = await _client_key(app)
        async with client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=DOWN)
                respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=OLLAMA_UNARY))
                resp = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"  # canonical schema intact across the fallback
    assert body["choices"][0]["message"]["content"] == "from ollama fallback"


async def test_all_providers_failing_returns_clean_502(postgres_url: str, redis_url: str) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        client, headers = await _client_key(app)
        async with client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=DOWN)
                respx.post(OLLAMA_URL).mock(return_value=DOWN)
                resp = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                )
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "api_error"


async def test_streaming_falls_back_before_first_byte(postgres_url: str, redis_url: str) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        client, headers = await _client_key(app)
        async with client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=DOWN)
                respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, text=OLLAMA_STREAM))
                resp = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                    },
                )
    assert resp.status_code == 200
    assert resp.text.endswith("data: [DONE]\n\n")
    content = ""
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line[6:] != "[DONE]":
            content += json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
    assert content == "Hello fallback"
