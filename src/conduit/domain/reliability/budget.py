"""Budget policy (pure): compare period spend against a cap.

The spend total and the current time are supplied by the caller (the service
reads them from Postgres / the clock); this module only does the comparison and
the period-window math. Pure domain — stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    spent: Decimal
    limit: Decimal
    remaining: Decimal


def period_start(now: datetime, period: str) -> datetime:
    """Start of the current spend window for ``period`` (UTC-normalised)."""
    if period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # daily (default)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def check_budget(*, spent: Decimal, limit: Decimal) -> BudgetDecision:
    """Allow while accumulated spend is strictly under the cap."""
    return BudgetDecision(
        allowed=spent < limit,
        spent=spent,
        limit=limit,
        remaining=limit - spent,
    )
