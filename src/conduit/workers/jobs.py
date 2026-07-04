"""Background jobs (arq).

Each job takes the arq ``ctx`` (shared clients wired at worker startup) and is
directly callable in tests with a hand-built ctx — no running worker needed.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conduit.domain.reliability.breaker import CircuitBreaker
from conduit.infra.db.models import UsageRecord, UsageRollup
from conduit.infra.redis import ProviderHealthStore
from conduit.providers.registry import ProviderRegistry

logger = structlog.get_logger("conduit.worker")


async def probe_providers(ctx: dict[str, Any]) -> int:
    """Probe every provider's health, store it, and feed the breaker on failure."""
    registry: ProviderRegistry = ctx["registry"]
    health_store: ProviderHealthStore = ctx["health_store"]
    breaker: CircuitBreaker = ctx["breaker"]
    now = time.time()
    names = registry.names()
    for name in names:
        status = await registry.get(name).health()
        await health_store.set_health(name, status.healthy, status.detail, now)
        if not status.healthy:
            await breaker.record_failure(name)
        logger.info("provider health probe", provider=name, healthy=status.healthy)
    return len(names)


async def rollup_usage(ctx: dict[str, Any]) -> int:
    """Aggregate the usage ledger into per-org daily rollups (upsert)."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    day_col = func.date(UsageRecord.created_at).label("day")
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    UsageRecord.org_id,
                    day_col,
                    func.count().label("requests"),
                    func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("tokens"),
                    func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost"),
                ).group_by(UsageRecord.org_id, day_col)
            )
        ).all()
        for org_id, day, requests, tokens, cost in rows:
            existing = await session.scalar(
                select(UsageRollup).where(UsageRollup.org_id == org_id, UsageRollup.day == day)
            )
            if existing is None:
                session.add(
                    UsageRollup(
                        org_id=org_id,
                        day=day,
                        request_count=int(requests),
                        total_tokens=int(tokens),
                        total_cost_usd=Decimal(cost),
                    )
                )
            else:
                existing.request_count = int(requests)
                existing.total_tokens = int(tokens)
                existing.total_cost_usd = Decimal(cost)
        await session.commit()
        return len(rows)
