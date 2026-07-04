"""Integration test: in-flight single-flight dedup (real Redis + respx-mocked provider)."""

from __future__ import annotations

import asyncio

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
        {"index": 0, "message": {"role": "assistant", "content": "42"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12},
}
BODY = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "q"}], "temperature": 0}


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def test_concurrent_identical_requests_collapse_to_one_upstream_call(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, openai_api_key=UPSTREAM_KEY
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        headers = {"Authorization": f"Bearer {key}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            calls = 0

            async def slow_upstream(request: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.2)  # hold the leader so waiters pile up
                return httpx.Response(200, json=OPENAI_RESPONSE)

            with respx.mock:
                respx.post(OPENAI_URL).mock(side_effect=slow_upstream)
                results = await asyncio.gather(
                    *(
                        client.post("/v1/chat/completions", headers=headers, json=BODY)
                        for _ in range(5)
                    )
                )
    assert all(r.status_code == 200 for r in results)
    assert all(r.json()["choices"][0]["message"]["content"] == "42" for r in results)
    assert calls == 1  # five concurrent identical requests → one upstream call


async def test_failure_propagates_to_all_waiters_and_is_not_cached(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key=UPSTREAM_KEY,
        retry_max_attempts=1,  # keep the failing batch to a single upstream call
        breaker_enabled=False,
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        headers = {"Authorization": f"Bearer {key}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            calls = 0

            async def failing_upstream(request: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.2)
                return httpx.Response(500, json={"error": "boom"})

            with respx.mock:
                respx.post(OPENAI_URL).mock(side_effect=failing_upstream)
                batch = await asyncio.gather(
                    *(
                        client.post("/v1/chat/completions", headers=headers, json=BODY)
                        for _ in range(4)
                    )
                )
                # Failure was not cached — a follow-up request re-executes upstream.
                followup = await client.post("/v1/chat/completions", headers=headers, json=BODY)
    assert all(r.status_code >= 500 for r in batch)  # every waiter got the error
    assert followup.status_code >= 500
    assert calls == 2  # one for the collapsed failing batch, one for the follow-up
