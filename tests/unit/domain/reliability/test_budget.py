"""Unit tests for the pure budget policy."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from conduit.domain.reliability.budget import check_budget, period_start


def test_under_budget_is_allowed() -> None:
    decision = check_budget(spent=Decimal("4.50"), limit=Decimal("10"))
    assert decision.allowed is True
    assert decision.remaining == Decimal("5.50")


def test_at_or_over_budget_is_denied() -> None:
    assert check_budget(spent=Decimal("10"), limit=Decimal("10")).allowed is False
    assert check_budget(spent=Decimal("10.01"), limit=Decimal("10")).allowed is False


def test_period_start_daily() -> None:
    now = datetime(2026, 7, 5, 13, 30, 45, tzinfo=UTC)
    assert period_start(now, "daily") == datetime(2026, 7, 5, 0, 0, 0, tzinfo=UTC)


def test_period_start_monthly() -> None:
    now = datetime(2026, 7, 5, 13, 30, 45, tzinfo=UTC)
    assert period_start(now, "monthly") == datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)
