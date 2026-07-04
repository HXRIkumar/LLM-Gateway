"""Unit tests for the pure token-bucket policy (injected clock, no I/O)."""

from __future__ import annotations

import pytest

from conduit.domain.reliability.ratelimit import RateLimit, token_bucket


def test_admits_up_to_capacity_then_denies() -> None:
    limit = RateLimit(requests=3, window_seconds=60)
    tokens, last = limit.capacity, 0.0
    outcomes = []
    for _ in range(4):
        tokens, last, result = token_bucket(tokens=tokens, last_refill=last, now=0.0, limit=limit)
        outcomes.append(result.allowed)
    assert outcomes == [True, True, True, False]


def test_denied_reports_retry_after() -> None:
    limit = RateLimit(requests=10, window_seconds=10)  # 1 token/sec
    _, _, result = token_bucket(tokens=0.0, last_refill=0.0, now=0.0, limit=limit)
    assert result.allowed is False
    assert result.retry_after_seconds == pytest.approx(1.0)


def test_refills_over_time() -> None:
    limit = RateLimit(requests=2, window_seconds=2)  # 1 token/sec, capacity 2
    # Empty bucket at t=0 → denied.
    _, _, denied = token_bucket(tokens=0.0, last_refill=0.0, now=0.0, limit=limit)
    assert denied.allowed is False
    # One second later, one token has refilled → admitted.
    _, _, allowed = token_bucket(tokens=0.0, last_refill=0.0, now=1.0, limit=limit)
    assert allowed.allowed is True


def test_does_not_overfill_past_capacity() -> None:
    limit = RateLimit(requests=5, window_seconds=5)
    # Long idle shouldn't accumulate more than capacity.
    tokens, _, result = token_bucket(tokens=5.0, last_refill=0.0, now=10_000.0, limit=limit)
    assert result.allowed is True
    assert tokens <= limit.capacity
