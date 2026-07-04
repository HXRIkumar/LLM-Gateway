"""Integration tests for the shared Redis circuit breaker (real Redis)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conduit.config import Settings
from conduit.domain.reliability.breaker import BreakerConfig
from conduit.infra.redis import RedisCircuitBreaker, create_redis_client
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
BODY = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}


async def test_breaker_opens_then_half_opens_then_closes(redis_url: str) -> None:
    client = create_redis_client(Settings(redis_url=redis_url))
    breaker = RedisCircuitBreaker(client, BreakerConfig(failure_threshold=2, cooldown_seconds=30.0))
    provider = "prov-lifecycle"
    try:
        assert await breaker.allow(provider, now=1000.0) is True
        await breaker.record_failure(provider, now=1000.0)
        assert await breaker.allow(provider, now=1000.0) is True  # 1 failure, still closed
        await breaker.record_failure(provider, now=1000.0)
        assert await breaker.allow(provider, now=1000.0) is False  # 2 → open, fast-fail
        assert await breaker.allow(provider, now=1031.0) is True  # cooldown elapsed → half-open
        await breaker.record_success(provider, now=1031.0)
        assert await breaker.allow(provider, now=1031.0) is True  # closed again
    finally:
        await client.aclose()


async def test_half_open_probe_failure_reopens(redis_url: str) -> None:
    client = create_redis_client(Settings(redis_url=redis_url))
    breaker = RedisCircuitBreaker(client, BreakerConfig(failure_threshold=1, cooldown_seconds=10.0))
    provider = "prov-reopen"
    try:
        await breaker.record_failure(provider, now=2000.0)  # threshold 1 → open
        assert await breaker.allow(provider, now=2000.0) is False
        assert await breaker.allow(provider, now=2011.0) is True  # half-open probe
        await breaker.record_failure(provider, now=2011.0)  # probe fails → open
        assert await breaker.allow(provider, now=2011.0) is False
    finally:
        await client.aclose()


async def _flush_breakers(redis_url: str) -> None:
    client = create_redis_client(Settings(redis_url=redis_url))
    try:
        keys = await client.keys("breaker:*")
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def test_gateway_opens_breaker_and_fast_fails(postgres_url: str, redis_url: str) -> None:
    await _flush_breakers(redis_url)
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key="sk-test",
        rate_limit_enabled=False,
        retry_max_attempts=1,
        breaker_failure_threshold=2,
        breaker_cooldown_seconds=30.0,
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        headers = {"Authorization": f"Bearer {key}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                route = respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(503, json={"error": {"message": "down"}})
                )
                first = await client.post("/v1/chat/completions", headers=headers, json=BODY)
                second = await client.post("/v1/chat/completions", headers=headers, json=BODY)
                third = await client.post("/v1/chat/completions", headers=headers, json=BODY)

    assert first.status_code == 502
    assert second.status_code == 502
    assert third.status_code == 502
    # The breaker opened after 2 failures — the 3rd request never reached the provider.
    assert route.call_count == 2
