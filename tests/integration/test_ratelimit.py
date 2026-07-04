"""Integration tests for Redis token-bucket rate limiting (real Redis)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conduit.config import Settings
from conduit.domain.reliability.ratelimit import RateLimit
from conduit.infra.redis import RedisRateLimiter, create_redis_client
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


async def test_admits_capacity_then_429s(redis_url: str) -> None:
    client = create_redis_client(Settings(redis_url=redis_url))
    try:
        limiter = RedisRateLimiter(client)
        limit = RateLimit(requests=3, window_seconds=60)
        outcomes = [(await limiter.check("rl:admit", limit, now=1000.0)).allowed for _ in range(4)]
        assert outcomes == [True, True, True, False]
        denied = await limiter.check("rl:admit", limit, now=1000.0)
        assert denied.retry_after_seconds > 0
    finally:
        await client.aclose()


async def test_refills_over_time(redis_url: str) -> None:
    client = create_redis_client(Settings(redis_url=redis_url))
    try:
        limiter = RedisRateLimiter(client)
        limit = RateLimit(requests=2, window_seconds=2)  # 1 token/sec
        assert (await limiter.check("rl:refill", limit, now=1000.0)).allowed
        assert (await limiter.check("rl:refill", limit, now=1000.0)).allowed
        assert not (await limiter.check("rl:refill", limit, now=1000.0)).allowed
        # 2s later the bucket has refilled.
        assert (await limiter.check("rl:refill", limit, now=1002.0)).allowed
    finally:
        await client.aclose()


async def test_atomic_under_concurrency(redis_url: str) -> None:
    client = create_redis_client(Settings(redis_url=redis_url))
    try:
        limiter = RedisRateLimiter(client)
        limit = RateLimit(requests=5, window_seconds=60)
        results = await asyncio.gather(
            *[limiter.check("rl:concurrent", limit, now=2000.0) for _ in range(20)]
        )
        # Exactly capacity admitted — the Lua op prevents over-admission.
        assert sum(1 for r in results if r.allowed) == 5
    finally:
        await client.aclose()


async def test_fails_open_when_redis_unavailable() -> None:
    client = create_redis_client(Settings(redis_url="redis://127.0.0.1:1/0"))
    try:
        limiter = RedisRateLimiter(client)
        result = await limiter.check("rl:down", RateLimit(requests=1, window_seconds=60))
        assert result.allowed is True  # fail-open, availability over strict limiting
    finally:
        await client.aclose()


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def test_endpoint_returns_429_with_retry_after(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key="sk-test",
        rate_limit_per_key_requests=1,
        rate_limit_per_key_window_seconds=60,
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        headers = {"Authorization": f"Bearer {key}"}
        body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                first = await client.post("/v1/chat/completions", headers=headers, json=body)
                second = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "rate_limit_error"
    assert "retry-after" in {k.lower() for k in second.headers}
