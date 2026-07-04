"""Token-bucket rate-limiting policy (pure) and the ``RateLimiter`` port.

The algorithm here is the reference, unit-tested with an injected clock. The
*atomic* implementation lives in an infra adapter (a Redis Lua script), because
only a single-round-trip atomic op can stop concurrent requests over-admitting;
this pure function mirrors that algorithm so its behaviour is testable without
I/O. Pure domain: no framework, no vendor, no clock of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A limit of ``requests`` per ``window_seconds`` (a full bucket = capacity)."""

    requests: int
    window_seconds: float

    @property
    def capacity(self) -> float:
        return float(self.requests)

    @property
    def refill_per_second(self) -> float:
        return self.requests / self.window_seconds if self.window_seconds > 0 else 0.0


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a limit check."""

    allowed: bool
    retry_after_seconds: float
    remaining: int


def token_bucket(
    *,
    tokens: float,
    last_refill: float,
    now: float,
    limit: RateLimit,
    cost: float = 1.0,
) -> tuple[float, float, RateLimitResult]:
    """Advance a token bucket and decide one request.

    Returns ``(new_tokens, new_last_refill, result)``. ``retry_after_seconds`` is
    how long until enough tokens refill for the request when denied.
    """
    refill = limit.refill_per_second
    elapsed = now - last_refill if now > last_refill else 0.0
    filled = min(limit.capacity, tokens + elapsed * refill)

    if filled >= cost:
        remaining = filled - cost
        return remaining, now, RateLimitResult(True, 0.0, int(remaining))

    retry_after = (cost - filled) / refill if refill > 0 else float("inf")
    return filled, now, RateLimitResult(False, retry_after, int(filled))


class RateLimiter(Protocol):
    """Port: atomically check-and-consume one token for a scope."""

    async def check(self, scope: str, limit: RateLimit) -> RateLimitResult: ...
