"""Admin API for per-org spend budgets (guarded by the bootstrap admin key)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from conduit.api.deps import BudgetServiceDep
from conduit.api.middleware import require_admin
from conduit.infra.db.models import BudgetPeriod

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class BudgetIn(BaseModel):
    """Create/update a budget. ``org_id`` defaults to the default org."""

    limit_usd: Decimal
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    org_id: uuid.UUID | None = None


class BudgetOut(BaseModel):
    org_id: str
    limit_usd: str
    period: str
    status: str


@router.post("/budgets", status_code=201)
async def set_budget(payload: BudgetIn, budgets: BudgetServiceDep) -> BudgetOut:
    budget = await budgets.set_budget(
        limit_usd=payload.limit_usd, period=str(payload.period), org_id=payload.org_id
    )
    return BudgetOut(
        org_id=str(budget.org_id),
        limit_usd=str(budget.limit_usd),
        period=budget.period,
        status=budget.status,
    )


@router.get("/budgets")
async def list_budgets(budgets: BudgetServiceDep) -> list[dict[str, object]]:
    return await budgets.list_with_spend()
