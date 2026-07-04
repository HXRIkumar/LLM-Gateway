"""Background jobs (arq).

Each job takes the arq ``ctx`` (shared clients wired at worker startup) and is
directly callable in tests with a hand-built ctx — no running worker needed.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conduit.domain.reliability.breaker import CircuitBreaker
from conduit.infra.db.models import UsageRecord, UsageRollup
from conduit.infra.redis import ProviderHealthStore, RedisRouteStats
from conduit.providers.registry import ProviderRegistry

# Rolling window and TTL for adaptive-routing error-rate aggregates.
_ROUTE_STATS_WINDOW = timedelta(minutes=15)
_ROUTE_STATS_TTL_SECONDS = 900

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


async def refresh_route_stats(ctx: dict[str, Any]) -> int:
    """Maintain rolling per-(provider, model) error rates for adaptive routing.

    Computes the recent error rate from the usage ledger and writes it to Redis,
    where the balanced router reads it to de-weight providers whose error rate is
    climbing (and let them recover as the rate falls back).
    """
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    route_stats: RedisRouteStats = ctx["route_stats"]
    since = datetime.now(UTC) - _ROUTE_STATS_WINDOW
    errors = func.sum(case((UsageRecord.status != "ok", 1), else_=0))
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    UsageRecord.provider,
                    UsageRecord.model,
                    func.count().label("total"),
                    errors.label("errors"),
                )
                .where(UsageRecord.created_at >= since)
                .group_by(UsageRecord.provider, UsageRecord.model)
            )
        ).all()
    for provider, model, total, error_count in rows:
        rate = (int(error_count) / int(total)) if total else 0.0
        await route_stats.set_error_rate(provider, model, rate, _ROUTE_STATS_TTL_SECONDS)
    return len(rows)


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
