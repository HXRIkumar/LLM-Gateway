"""Integration tests for routing-policy persistence + resolution (real Postgres)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conduit.config import Settings
from conduit.main import create_app, lifespan
from conduit.services.keys import IssuedKey, KeyService
from conduit.services.policies import PolicyService

pytestmark = pytest.mark.integration
ADMIN = {"Authorization": "Bearer admin-secret"}


@pytest.fixture(autouse=True)
async def _clean(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE routing_policy CASCADE"))
    finally:
        await engine.dispose()
    yield


async def _mint(app: FastAPI) -> IssuedKey:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org(f"pol-{uuid.uuid4().hex[:8]}")
        return await service.issue(org.id)


async def test_effective_policy_precedence(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    sessionmaker = async_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            key_service = KeyService(session)
            org = await key_service.get_or_create_default_org(f"pol-{uuid.uuid4().hex[:8]}")
            issued = await key_service.issue(org.id)
        service = PolicyService(sessionmaker)

        # None set → neutral default.
        default = await service.effective(org_id=issued.org_id, api_key_id=issued.id)
        assert default.objective == "balanced"
        assert default.allow_providers is None

        # Org policy applies when no key policy.
        await service.upsert(objective="cost", allow_providers=["ollama"], org_id=issued.org_id)
        org_level = await service.effective(org_id=issued.org_id, api_key_id=issued.id)
        assert org_level.objective == "cost"
        assert org_level.allow_providers == ("ollama",)

        # Key policy overrides org policy.
        await service.upsert(objective="latency", api_key_id=issued.id)
        key_level = await service.effective(org_id=issued.org_id, api_key_id=issued.id)
        assert key_level.objective == "latency"
    finally:
        await engine.dispose()


async def test_admin_policy_endpoints(postgres_url: str) -> None:
    settings = Settings(database_url=postgres_url, admin_api_key="admin-secret")  # type: ignore[call-arg]
    app = create_app(settings)
    async with lifespan(app):
        issued = await _mint(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.post("/v1/admin/policies", json={})).status_code == 401

            resp = await client.post(
                "/v1/admin/policies",
                headers=ADMIN,
                json={
                    "objective": "cost",
                    "allow_providers": ["openai"],
                    "org_id": str(issued.org_id),
                },
            )
            assert resp.status_code == 201
            assert resp.json()["objective"] == "cost"
            assert resp.json()["scope"] == f"org:{issued.org_id}"

            listed = (await client.get("/v1/admin/policies", headers=ADMIN)).json()
            assert any(p["objective"] == "cost" for p in listed)
