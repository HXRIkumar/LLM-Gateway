"""Integration tests: error-rate adaptive routing + the worker that maintains it."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conduit.config import Settings
from conduit.infra.db.models import UsageRecord
from conduit.infra.redis import RedisRouteStats, create_redis_client
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService
from conduit.workers.jobs import refresh_route_stats

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"
ALIASES = {"smart": ["gpt-4o-mini", "llama3.2"]}

OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "from-openai"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}
OLLAMA_RESPONSE = {
    "model": "llama3.2",
    "message": {"role": "assistant", "content": "from-ollama"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 5,
    "eval_count": 2,
}


@pytest.fixture(autouse=True)
async def _clean_usage(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE usage_record"))
    finally:
        await engine.dispose()
    yield


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def _route_smart(client: AsyncClient, key: str) -> str:
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    return resp.json()["choices"][0]["message"]["content"]


async def test_routing_shifts_away_from_high_error_provider_and_recovers(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key="upstream-credential-do-not-log-12345",
        model_aliases=ALIASES,
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))

                # Degrade ollama (the otherwise-cheapest pick) → routing avoids it.
                await app.state.redis.set("routestats:ollama:llama3.2", "0.9")
                degraded = await _route_smart(client, key)

                # Recover ollama → routing returns to it.
                await app.state.redis.set("routestats:ollama:llama3.2", "0.0")
                recovered = await _route_smart(client, key)

    assert degraded == "from-openai"  # shifted away from the degraded provider
    assert recovered == "from-ollama"  # returned once its error rate recovered


async def test_worker_maintains_rolling_error_rate(postgres_url: str, redis_url: str) -> None:
    engine = create_async_engine(postgres_url)
    sessionmaker = async_sessionmaker(engine)
    redis = create_redis_client(Settings(redis_url=redis_url))
    try:
        async with sessionmaker() as session:
            key_service = KeyService(session)
            org = await key_service.get_or_create_default_org(f"adapt-{uuid.uuid4().hex[:8]}")
            issued = await key_service.issue(org.id)

        # 4 requests for openai/gpt-4o-mini: 1 error, 3 ok → error rate 0.25.
        async with sessionmaker() as session:
            for status in ("ok", "ok", "ok", "error"):
                session.add(
                    UsageRecord(
                        org_id=issued.org_id,
                        api_key_id=issued.id,
                        provider="openai",
                        model="gpt-4o-mini",
                        total_tokens=1,
                        latency_ms=1,
                        status=status,
                        created_at=datetime.now(UTC),
                    )
                )
            await session.commit()

        groups = await refresh_route_stats(
            {"sessionmaker": sessionmaker, "route_stats": RedisRouteStats(redis)}
        )
        assert groups == 1
        rates = await RedisRouteStats(redis).error_rates([("openai", "gpt-4o-mini")])
    finally:
        await redis.aclose()
        await engine.dispose()

    assert rates[("openai", "gpt-4o-mini")] == pytest.approx(0.25)
