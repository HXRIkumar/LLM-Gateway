"""Integration tests for retry around provider execution (respx)."""

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
OK_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
BODY = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}


def _app(postgres_url: str, redis_url: str) -> FastAPI:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key="sk-test",
        rate_limit_enabled=False,
        retry_base_delay_seconds=0.0,  # no real waiting in tests
        retry_max_delay_seconds=0.0,
    )
    return create_app(settings)


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def test_transient_5xx_is_retried_then_succeeds(postgres_url: str, redis_url: str) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                route = respx.post(OPENAI_URL).mock(
                    side_effect=[
                        httpx.Response(503, json={"error": {"message": "overloaded"}}),
                        httpx.Response(200, json=OK_RESPONSE),
                    ]
                )
                resp = await client.post(
                    "/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=BODY
                )
    assert resp.status_code == 200
    assert route.call_count == 2  # first failed, retried, then succeeded


async def test_client_4xx_is_not_retried(postgres_url: str, redis_url: str) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                route = respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
                )
                resp = await client.post(
                    "/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=BODY
                )
    assert resp.status_code == 400
    assert route.call_count == 1  # terminal — not retried
