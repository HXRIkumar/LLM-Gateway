"""Bounded-retry policy (pure).

Classification and backoff math are pure and unit-tested with an injected RNG;
the retry combinator awaits an injected ``fn`` and ``sleep`` so it does no real
I/O of its own (the domain stays free of frameworks and clocks). Retries apply
only to transient upstream failures and never once a response has begun
streaming — the streaming path simply isn't retried.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from conduit.domain.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    UpstreamInvalidRequest,
)

# Bad-request and upstream-auth failures won't succeed on retry — terminal.
_TERMINAL: tuple[type[Exception], ...] = (UpstreamInvalidRequest, ProviderAuthError)
# Timeouts and upstream rate-limits are transient — retryable.
_RETRYABLE: tuple[type[Exception], ...] = (ProviderTimeout, ProviderRateLimited)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff with full jitter."""

    max_attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 5.0


def is_retryable(exc: Exception) -> bool:
    """A transient upstream failure worth retrying against the same provider."""
    if isinstance(exc, _TERMINAL):
        return False
    if isinstance(exc, _RETRYABLE):
        return True
    # Generic provider failures (5xx, connection errors) are transient.
    return isinstance(exc, ProviderError)


def compute_delay(policy: RetryPolicy, attempt: int, rng: random.Random) -> float:
    """Full-jitter backoff: uniform(0, min(max_delay, base * 2**attempt))."""
    ceiling = min(policy.max_delay, policy.base_delay * (2**attempt))
    return rng.uniform(0.0, ceiling)


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], Awaitable[None]],
    rng: random.Random,
) -> T:
    """Call ``fn`` up to ``max_attempts`` times, backing off between retryable failures."""
    for attempt in range(policy.max_attempts):
        try:
            return await fn()
        except Exception as exc:
            if not is_retryable(exc) or attempt == policy.max_attempts - 1:
                raise
            await sleep(compute_delay(policy, attempt, rng))
    raise RuntimeError("retry loop exhausted")  # pragma: no cover - unreachable
