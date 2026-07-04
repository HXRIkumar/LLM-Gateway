"""Unit tests for the pure circuit-breaker state machine."""

from __future__ import annotations

from conduit.domain.reliability.breaker import (
    BreakerConfig,
    BreakerSnapshot,
    BreakerState,
    evaluate,
    on_failure,
    on_success,
)

CONFIG = BreakerConfig(failure_threshold=3, cooldown_seconds=30.0)


def test_closed_admits() -> None:
    allowed, snap = evaluate(BreakerSnapshot(), now=0.0, config=CONFIG)
    assert allowed is True
    assert snap.state is BreakerState.CLOSED


def test_failures_trip_open_at_threshold() -> None:
    snap = BreakerSnapshot()
    snap = on_failure(snap, now=0.0, config=CONFIG)
    assert snap.state is BreakerState.CLOSED  # 1
    snap = on_failure(snap, now=0.0, config=CONFIG)
    assert snap.state is BreakerState.CLOSED  # 2
    snap = on_failure(snap, now=10.0, config=CONFIG)
    assert snap.state is BreakerState.OPEN and snap.opened_at == 10.0  # 3 → open


def test_open_denies_within_cooldown() -> None:
    snap = BreakerSnapshot(state=BreakerState.OPEN, failures=3, opened_at=100.0)
    allowed, result = evaluate(snap, now=120.0, config=CONFIG)  # 20s < 30
    assert allowed is False
    assert result.state is BreakerState.OPEN


def test_open_half_opens_after_cooldown() -> None:
    snap = BreakerSnapshot(state=BreakerState.OPEN, failures=3, opened_at=100.0)
    allowed, result = evaluate(snap, now=131.0, config=CONFIG)  # 31s >= 30
    assert allowed is True
    assert result.state is BreakerState.HALF_OPEN


def test_success_closes_and_resets() -> None:
    snap = BreakerSnapshot(state=BreakerState.HALF_OPEN, failures=3, opened_at=100.0)
    result = on_success(snap)
    assert result.state is BreakerState.CLOSED
    assert result.failures == 0


def test_half_open_failure_reopens_immediately() -> None:
    snap = BreakerSnapshot(state=BreakerState.HALF_OPEN, failures=3, opened_at=100.0)
    result = on_failure(snap, now=200.0, config=CONFIG)
    assert result.state is BreakerState.OPEN
    assert result.opened_at == 200.0
