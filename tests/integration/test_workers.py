"""Integration tests for the arq worker jobs (real Postgres + Redis)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conduit.config import Settings
from conduit.domain.reliability.breaker import BreakerConfig
from conduit.infra.db.models import UsageRecord, UsageRollup
from conduit.infra.redis import ProviderHealthStore, RedisCircuitBreaker, create_redis_client
from conduit.main import create_app, lifespan
from conduit.providers.registry import ProviderRegistry
from conduit.services.keys import KeyService
from conduit.workers.jobs import probe_providers, rollup_usage

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _clean_usage_tables(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE usage_record, usage_rollup"))
    finally:
        await engine.dispose()
    yield


async def test_probe_updates_health_and_breaker_state(redis_url: str, fake_provider_cls) -> None:
    client = create_redis_client(Settings(redis_url=redis_url))
    try:
        registry = ProviderRegistry({"flaky": fake_provider_cls(name="flaky", healthy=False)})
        health_store = ProviderHealthStore(client)
        breaker = RedisCircuitBreaker(
            client, BreakerConfig(failure_threshold=1, cooldown_seconds=30.0)
        )
        ctx: dict[str, Any] = {
            "registry": registry,
            "health_store": health_store,
            "breaker": breaker,
        }

        probed = await probe_providers(ctx)
        assert probed == 1

        health = await health_store.get_health("flaky")
        assert health is not None
        assert health["healthy"] is False

        # An unhealthy probe fed the breaker; threshold 1 → now open.
        assert await breaker.allow("flaky", now=0.0) is False
    finally:
        await client.aclose()


async def test_rollup_aggregates_usage_records(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    sessionmaker = async_sessionmaker(engine)
    try:
        # A real org + key so usage rows satisfy the FK.
        async with sessionmaker() as session:
            key_service = KeyService(session)
            org = await key_service.get_or_create_default_org(f"rollup-{uuid.uuid4().hex[:8]}")
            issued = await key_service.issue(org.id)
        org_id, api_key_id = issued.org_id, issued.id

        # Two requests for the same org on the same day.
        async with sessionmaker() as session:
            for cost, tokens in ((Decimal("0.10"), 100), (Decimal("0.25"), 200)):
                session.add(
                    UsageRecord(
                        org_id=org_id,
                        api_key_id=api_key_id,
                        provider="openai",
                        model="gpt-4o-mini",
                        total_tokens=tokens,
                        cost_usd=cost,
                        latency_ms=1,
                        status="ok",
                        created_at=datetime.now(UTC),
                    )
                )
            await session.commit()

        rolled = await rollup_usage({"sessionmaker": sessionmaker})
        assert rolled == 1  # one (org, day) group

        async with sessionmaker() as session:
            rollup = await session.scalar(select(UsageRollup).where(UsageRollup.org_id == org_id))
            assert rollup is not None
            assert rollup.request_count == 2
            assert rollup.total_tokens == 300
            assert rollup.total_cost_usd == Decimal("0.350000")
    finally:
        await engine.dispose()


async def test_admin_provider_health_endpoint(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, admin_api_key="admin-secret"
    )
    app = create_app(settings)
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.get("/v1/admin/providers/health")).status_code == 401
            resp = await client.get(
                "/v1/admin/providers/health", headers={"Authorization": "Bearer admin-secret"}
            )
            assert resp.status_code == 200
            names = {row["provider"] for row in resp.json()}
            assert {"openai", "ollama"} <= names
