"""Latency stats adapter — implements the domain ``LatencyStats`` port.

Reads rolling average latency per ``(provider, model)`` from the Phase 2 usage
ledger (Postgres). The domain routing side stays pure; this is where the DB lives.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conduit.infra.db.models import UsageRecord


class UsageLatencyStats:
    """Rolling mean latency per provider/model from ``usage_record``."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        window: timedelta = timedelta(hours=1),
    ) -> None:
        self._sessionmaker = sessionmaker
        self._window = window

    async def snapshot(self, targets: Sequence[tuple[str, str]]) -> dict[tuple[str, str], float]:
        if not targets:
            return {}
        since = datetime.now(UTC) - self._window
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(
                        UsageRecord.provider,
                        UsageRecord.model,
                        func.avg(UsageRecord.latency_ms),
                    )
                    .where(UsageRecord.created_at >= since, UsageRecord.status == "ok")
                    .group_by(UsageRecord.provider, UsageRecord.model)
                )
            ).all()
        observed = {
            (provider, model): float(avg) for provider, model, avg in rows if avg is not None
        }
        wanted = set(targets)
        return {key: value for key, value in observed.items() if key in wanted}
