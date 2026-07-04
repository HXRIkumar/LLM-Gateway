"""Integration tests for the datastore foundation.

These run against real Postgres and Redis (testcontainers). They verify that the
migrated schema round-trips ORM rows, that the Redis probe and basic ops work,
and that ``/readyz`` reports healthy when both backends are actually reachable.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conduit.config import Settings
from conduit.infra.db.models import ApiKey, ApiKeyStatus, Organization
from conduit.infra.redis import check_redis, create_redis_client
from conduit.main import create_app, lifespan

pytestmark = pytest.mark.integration


async def test_session_round_trips_org_and_key(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            org = Organization(name="Acme Corp")
            session.add(org)
            await session.flush()
            org_id = org.id
            session.add(ApiKey(org_id=org_id, key_hash="hash-abc", prefix="ck_1234"))
            await session.commit()

        async with sessionmaker() as session:
            fetched = await session.get(Organization, org_id)
            assert fetched is not None
            assert fetched.name == "Acme Corp"

            keys = list(await session.scalars(select(ApiKey).where(ApiKey.org_id == org_id)))
            assert len(keys) == 1
            assert keys[0].prefix == "ck_1234"
            assert keys[0].status == ApiKeyStatus.ACTIVE  # server default applied
            assert keys[0].created_at is not None
            assert keys[0].revoked_at is None
    finally:
        await engine.dispose()


async def test_redis_ping_and_round_trip(redis_url: str) -> None:
    client = create_redis_client(Settings(redis_url=redis_url))
    try:
        assert await check_redis(client) is True
        await client.set("conduit:test-key", "ok")
        assert await client.get("conduit:test-key") == "ok"
    finally:
        await client.aclose()


async def test_readyz_ok_against_real_backends(postgres_url: str, redis_url: str) -> None:
    settings = Settings(database_url=postgres_url, redis_url=redis_url)
    app = create_app(settings)
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["dependencies"] == {"database": "ok", "redis": "ok"}
