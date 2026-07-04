"""Integration test for the usage-backed latency stats adapter (real Postgres)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conduit.infra.db.models import UsageRecord
from conduit.services.keys import KeyService
from conduit.services.stats import UsageLatencyStats

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _clean(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE usage_record"))
    finally:
        await engine.dispose()
    yield


async def test_snapshot_returns_rolling_average_latency(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    sessionmaker = async_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            key_service = KeyService(session)
            org = await key_service.get_or_create_default_org()
            issued = await key_service.issue(org.id)

        async with sessionmaker() as session:
            for provider, model, latency in (
                ("openai", "gpt-4o-mini", 100),
                ("openai", "gpt-4o-mini", 300),  # avg 200
                ("ollama", "llama3.2", 50),
            ):
                session.add(
                    UsageRecord(
                        org_id=issued.org_id,
                        api_key_id=issued.id,
                        provider=provider,
                        model=model,
                        cost_usd=Decimal("0"),
                        latency_ms=latency,
                        status="ok",
                    )
                )
            await session.commit()

        stats = UsageLatencyStats(sessionmaker)
        snapshot = await stats.snapshot(
            [("openai", "gpt-4o-mini"), ("ollama", "llama3.2"), ("nope", "nope")]
        )
        assert snapshot[("openai", "gpt-4o-mini")] == pytest.approx(200.0)
        assert snapshot[("ollama", "llama3.2")] == pytest.approx(50.0)
        assert ("nope", "nope") not in snapshot  # no data → absent
    finally:
        await engine.dispose()
