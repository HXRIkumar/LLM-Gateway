"""Circuit-breaker state machine (pure) and the ``CircuitBreaker`` port.

The transitions here are the reference, unit-tested; the *shared, atomic* state
lives per-provider in Redis via an infra adapter (a Lua script) so every worker
agrees. Closed → (failures reach threshold) → Open → (cooldown elapses) →
Half-Open → (probe succeeds → Closed | probe fails → Open).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    failure_threshold: int = 5
    cooldown_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class BreakerSnapshot:
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at: float = 0.0


def evaluate(
    snapshot: BreakerSnapshot, now: float, config: BreakerConfig
) -> tuple[bool, BreakerSnapshot]:
    """Decide whether a call is allowed; OPEN flips to HALF_OPEN after cooldown."""
    if snapshot.state is BreakerState.OPEN:
        if now - snapshot.opened_at >= config.cooldown_seconds:
            return True, replace(snapshot, state=BreakerState.HALF_OPEN)
        return False, snapshot
    return True, snapshot  # CLOSED or HALF_OPEN admit


def on_success(snapshot: BreakerSnapshot) -> BreakerSnapshot:
    """Any success closes the breaker and clears the failure count."""
    return BreakerSnapshot()


def on_failure(snapshot: BreakerSnapshot, now: float, config: BreakerConfig) -> BreakerSnapshot:
    """A failure trips the breaker at the threshold, or immediately from HALF_OPEN."""
    failures = snapshot.failures + 1
    if snapshot.state is BreakerState.HALF_OPEN or failures >= config.failure_threshold:
        return BreakerSnapshot(state=BreakerState.OPEN, failures=failures, opened_at=now)
    return replace(snapshot, failures=failures)


class CircuitBreaker(Protocol):
    """Port: shared per-provider breaker state."""

    async def allow(self, provider: str) -> bool: ...
    async def record_success(self, provider: str) -> None: ...
    async def record_failure(self, provider: str) -> None: ...
