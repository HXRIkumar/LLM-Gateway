"""Unit tests for the pure retry policy (injected clock + rng)."""

from __future__ import annotations

import random

import pytest

from conduit.domain.errors import (
    AuthError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    UpstreamInvalidRequest,
)
from conduit.domain.reliability.retry import (
    RetryPolicy,
    compute_delay,
    is_retryable,
    retry_async,
)


def test_classification_of_retryable_vs_terminal() -> None:
    assert is_retryable(ProviderTimeout("x")) is True
    assert is_retryable(ProviderRateLimited("x")) is True
    assert is_retryable(ProviderError("x")) is True  # generic 5xx/connect
    assert is_retryable(UpstreamInvalidRequest("x")) is False  # client 4xx
    assert is_retryable(ProviderAuthError("x")) is False  # our creds — retry won't help
    assert is_retryable(AuthError("x")) is False
    assert is_retryable(ValueError("x")) is False


def test_backoff_is_bounded_by_max_delay() -> None:
    policy = RetryPolicy(max_attempts=6, base_delay=0.1, max_delay=1.0)
    rng = random.Random(1234)
    for attempt in range(6):
        assert 0.0 <= compute_delay(policy, attempt, rng) <= 1.0


def test_backoff_ceiling_grows_then_caps() -> None:
    class MaxRng(random.Random):
        def uniform(self, a: float, b: float) -> float:
            return b

    policy = RetryPolicy(base_delay=1.0, max_delay=4.0)
    rng = MaxRng()
    assert compute_delay(policy, 0, rng) == 1.0
    assert compute_delay(policy, 1, rng) == 2.0
    assert compute_delay(policy, 2, rng) == 4.0
    assert compute_delay(policy, 3, rng) == 4.0  # capped


async def test_succeeds_after_transient_failures() -> None:
    calls = 0
    delays: list[float] = []

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ProviderTimeout("transient")
        return "ok"

    result = await retry_async(
        fn,
        RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0),
        sleep=sleeper,
        rng=random.Random(0),
    )
    assert result == "ok"
    assert calls == 3
    assert len(delays) == 2  # slept before each of the 2 retries


async def test_gives_up_after_max_attempts() -> None:
    calls = 0

    async def sleeper(delay: float) -> None:
        return None

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise ProviderError("always fails")

    with pytest.raises(ProviderError):
        await retry_async(
            fn, RetryPolicy(max_attempts=3, base_delay=0.0), sleep=sleeper, rng=random.Random(0)
        )
    assert calls == 3


async def test_terminal_error_is_not_retried() -> None:
    calls = 0

    async def sleeper(delay: float) -> None:
        return None

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise UpstreamInvalidRequest("bad request")

    with pytest.raises(UpstreamInvalidRequest):
        await retry_async(fn, RetryPolicy(max_attempts=3), sleep=sleeper, rng=random.Random(0))
    assert calls == 1
