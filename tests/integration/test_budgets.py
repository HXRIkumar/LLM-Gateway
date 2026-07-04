"""Integration tests for budgets (real Postgres, mocked provider)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conduit.config import Settings
from conduit.infra.db.models import UsageRecord
from conduit.main import create_app, lifespan
from conduit.services.keys import IssuedKey, KeyService

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
ADMIN = {"Authorization": "Bearer admin-secret"}
BODY = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture(autouse=True)
async def _clean(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE usage_record, budget CASCADE"))
    finally:
        await engine.dispose()
    yield


def _app(postgres_url: str) -> FastAPI:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        openai_api_key="sk-test",
        admin_api_key="admin-secret",
        rate_limit_enabled=False,  # isolate budget behaviour
    )
    return create_app(settings)


async def _mint_key(app: FastAPI) -> IssuedKey:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        return await service.issue(org.id)


async def _insert_spend(
    postgres_url: str, org_id: uuid.UUID, api_key_id: uuid.UUID, cost: str, created_at: datetime
) -> None:
    engine = create_async_engine(postgres_url)
    try:
        async with async_sessionmaker(engine)() as session:
            session.add(
                UsageRecord(
                    org_id=org_id,
                    api_key_id=api_key_id,
                    provider="openai",
                    model="gpt-4o-mini",
                    cost_usd=Decimal(cost),
                    latency_ms=1,
                    status="ok",
                    created_at=created_at,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def test_over_budget_rejected_with_billing_envelope(postgres_url: str) -> None:
    app = _app(postgres_url)
    async with lifespan(app):
        issued = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Set a small daily budget for the org via the admin API.
            resp = await client.post(
                "/v1/admin/budgets",
                headers=ADMIN,
                json={"limit_usd": "0.50", "period": "daily", "org_id": str(issued.org_id)},
            )
            assert resp.status_code == 201

            # Accumulated spend this period exceeds the cap.
            await _insert_spend(postgres_url, issued.org_id, issued.id, "1.00", datetime.now(UTC))

            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                over = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {issued.plaintext}"},
                    json=BODY,
                )
    assert over.status_code == 429
    assert over.json()["error"]["type"] == "insufficient_quota"


async def test_spend_from_a_previous_period_does_not_count(postgres_url: str) -> None:
    app = _app(postgres_url)
    async with lifespan(app):
        issued = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post(
                "/v1/admin/budgets",
                headers=ADMIN,
                json={"limit_usd": "0.50", "period": "daily", "org_id": str(issued.org_id)},
            )
            # Big spend, but two days ago → outside the current daily window.
            await _insert_spend(
                postgres_url,
                issued.org_id,
                issued.id,
                "1.00",
                datetime.now(UTC) - timedelta(days=2),
            )
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                allowed = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {issued.plaintext}"},
                    json=BODY,
                )
    assert allowed.status_code == 200


async def test_budget_admin_endpoints(postgres_url: str) -> None:
    app = _app(postgres_url)
    async with lifespan(app):
        issued = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Unauthorized without the admin key.
            assert (
                await client.post("/v1/admin/budgets", json={"limit_usd": "1"})
            ).status_code == 401

            resp = await client.post(
                "/v1/admin/budgets",
                headers=ADMIN,
                json={"limit_usd": "12.50", "period": "monthly", "org_id": str(issued.org_id)},
            )
            assert resp.status_code == 201
            assert resp.json()["period"] == "monthly"

            listed = (await client.get("/v1/admin/budgets", headers=ADMIN)).json()
            entry = next(b for b in listed if b["org_id"] == str(issued.org_id))
            assert entry["limit_usd"] == "12.5000"
            assert entry["spent_usd"] == "0"
