"""Integration test: exact-match response cache (real Redis + respx-mocked provider)."""

from __future__ import annotations

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
UPSTREAM_KEY = "upstream-credential-do-not-log-12345"  # stand-in secret (not sk-)
OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "reply body"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
}


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


def _body(*, temperature: float | None, stream: bool = False) -> dict:
    body: dict = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "what is 2+2?"}],
    }
    if temperature is not None:
        body["temperature"] = temperature
    if stream:
        body["stream"] = True
    return body


async def test_identical_cacheable_request_served_from_cache(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, openai_api_key=UPSTREAM_KEY
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            headers = {"Authorization": f"Bearer {key}"}
            with respx.mock:
                route = respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=OPENAI_RESPONSE)
                )
                first = await client.post(
                    "/v1/chat/completions", headers=headers, json=_body(temperature=0)
                )
                second = await client.post(
                    "/v1/chat/completions", headers=headers, json=_body(temperature=0)
                )
    assert first.status_code == second.status_code == 200
    assert first.json()["choices"][0]["message"]["content"] == "reply body"
    assert second.json() == first.json()  # identical, byte-for-byte
    assert route.call_count == 1  # second request hit the cache, NOT the provider


async def test_non_cacheable_request_always_hits_provider(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, openai_api_key=UPSTREAM_KEY
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            headers = {"Authorization": f"Bearer {key}"}
            with respx.mock:
                route = respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=OPENAI_RESPONSE)
                )
                # temperature omitted → non-deterministic → never cached.
                await client.post(
                    "/v1/chat/completions", headers=headers, json=_body(temperature=None)
                )
                await client.post(
                    "/v1/chat/completions", headers=headers, json=_body(temperature=None)
                )
    assert route.call_count == 2


async def test_cache_control_no_store_bypasses_cache(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, openai_api_key=UPSTREAM_KEY
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            headers = {"Authorization": f"Bearer {key}", "Cache-Control": "no-store"}
            with respx.mock:
                route = respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=OPENAI_RESPONSE)
                )
                await client.post(
                    "/v1/chat/completions", headers=headers, json=_body(temperature=0)
                )
                await client.post(
                    "/v1/chat/completions", headers=headers, json=_body(temperature=0)
                )
    assert route.call_count == 2  # bypass on both → provider every time


async def test_cached_response_replays_as_sse_stream(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, openai_api_key=UPSTREAM_KEY
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            headers = {"Authorization": f"Bearer {key}"}
            with respx.mock:
                route = respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=OPENAI_RESPONSE)
                )
                # Populate the cache with a unary request.
                await client.post(
                    "/v1/chat/completions", headers=headers, json=_body(temperature=0)
                )
                # Same key (stream excluded from the key) → cached replay as SSE.
                streamed = await client.post(
                    "/v1/chat/completions", headers=headers, json=_body(temperature=0, stream=True)
                )
    assert route.call_count == 1  # streaming request served from cache, no upstream call
    assert streamed.headers["content-type"].startswith("text/event-stream")
    text = streamed.text
    assert "data: " in text
    assert "reply body" in text
    assert text.rstrip().endswith("data: [DONE]")
