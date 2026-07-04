"""Unit tests for the pure SingleFlight coordinator."""

from __future__ import annotations

import asyncio

import pytest

from conduit.domain.optimize.dedup import SingleFlight


async def test_concurrent_same_key_runs_once() -> None:
    sf = SingleFlight()
    calls = 0

    async def work() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "result"

    results = await asyncio.gather(*(sf.do("k", work) for _ in range(10)))
    assert results == ["result"] * 10
    assert calls == 1


async def test_different_keys_run_independently() -> None:
    sf = SingleFlight()
    calls = 0

    async def work() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return calls

    await asyncio.gather(sf.do("a", work), sf.do("b", work))
    assert calls == 2


async def test_failure_propagates_and_is_not_memoized() -> None:
    sf = SingleFlight()
    attempts = 0

    async def failing() -> str:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.02)
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await asyncio.gather(*(sf.do("k", failing) for _ in range(3)))
    # First in-flight batch shared one execution and all saw the error.
    assert attempts == 1

    # Not memoized — the next call re-executes.
    with pytest.raises(ValueError):
        await sf.do("k", failing)
    assert attempts == 2
