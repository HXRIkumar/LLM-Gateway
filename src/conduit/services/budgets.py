"""Budget enforcement — a preflight policy backed by the usage ledger.

Spend is summed from ``usage_record`` (the Postgres system of record) over the
current period; the pure ``check_budget`` decides admission. No budget configured
→ unlimited (returns ``None``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conduit.domain.reliability.budget import BudgetDecision, check_budget, period_start
from conduit.infra.db.models import Budget, UsageRecord
from conduit.services.orgs import get_or_create_org


class BudgetService:
    """Reads budgets + accumulated spend and evaluates the budget policy."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def _spend_since(
        self, session: AsyncSession, org_id: uuid.UUID, since: datetime
    ) -> Decimal:
        total = await session.scalar(
            select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(
                UsageRecord.org_id == org_id, UsageRecord.created_at >= since
            )
        )
        return Decimal(total) if total is not None else Decimal(0)

    async def check(
        self, org_id: uuid.UUID, *, now: datetime | None = None
    ) -> BudgetDecision | None:
        """Evaluate the org's budget for the current period, or None if unbudgeted."""
        moment = now if now is not None else datetime.now(UTC)
        async with self._sessionmaker() as session:
            budget = await session.scalar(select(Budget).where(Budget.org_id == org_id))
            if budget is None or budget.status != "active":
                return None
            spent = await self._spend_since(session, org_id, period_start(moment, budget.period))
            return check_budget(spent=spent, limit=budget.limit_usd)

    async def set_budget(
        self,
        *,
        limit_usd: Decimal,
        period: str,
        org_id: uuid.UUID | None = None,
    ) -> Budget:
        """Create or update the org's budget (default org when ``org_id`` is None)."""
        async with self._sessionmaker() as session:
            resolved = org_id if org_id is not None else (await get_or_create_org(session)).id
            budget = await session.scalar(select(Budget).where(Budget.org_id == resolved))
            if budget is None:
                budget = Budget(org_id=resolved, limit_usd=limit_usd, period=period)
                session.add(budget)
            else:
                budget.limit_usd = limit_usd
                budget.period = period
                budget.status = "active"
            await session.commit()
            await session.refresh(budget)
            return budget

    async def list_with_spend(self, *, now: datetime | None = None) -> list[dict[str, object]]:
        moment = now if now is not None else datetime.now(UTC)
        async with self._sessionmaker() as session:
            budgets = list(await session.scalars(select(Budget)))
            rows: list[dict[str, object]] = []
            for budget in budgets:
                spent = await self._spend_since(
                    session, budget.org_id, period_start(moment, budget.period)
                )
                rows.append(
                    {
                        "org_id": str(budget.org_id),
                        "limit_usd": str(budget.limit_usd),
                        "period": budget.period,
                        "status": budget.status,
                        "spent_usd": str(spent),
                        "remaining_usd": str(budget.limit_usd - spent),
                    }
                )
            return rows
