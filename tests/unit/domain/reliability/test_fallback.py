"""Unit tests for the pure fallback walker."""

from __future__ import annotations

import pytest

from conduit.domain.errors import AllProvidersFailed, ProviderTimeout, UpstreamInvalidRequest
from conduit.domain.reliability.fallback import walk_fallback
from conduit.domain.routing.strategy import RoutingTarget

PRIMARY = RoutingTarget(provider="openai", model="gpt-4o-mini")
BACKUP = RoutingTarget(provider="ollama", model="llama3.2")


async def test_primary_success_skips_fallback() -> None:
    attempted: list[RoutingTarget] = []

    async def attempt(target: RoutingTarget) -> str:
        attempted.append(target)
        return f"ok:{target.provider}"

    result, target = await walk_fallback([PRIMARY, BACKUP], attempt)
    assert result == "ok:openai"
    assert target == PRIMARY
    assert attempted == [PRIMARY]


async def test_retryable_failure_advances_to_backup() -> None:
    async def attempt(target: RoutingTarget) -> str:
        if target == PRIMARY:
            raise ProviderTimeout("down")
        return "ok:ollama"

    result, target = await walk_fallback([PRIMARY, BACKUP], attempt)
    assert result == "ok:ollama"
    assert target == BACKUP


async def test_terminal_failure_does_not_fall_back() -> None:
    attempted: list[RoutingTarget] = []

    async def attempt(target: RoutingTarget) -> str:
        attempted.append(target)
        raise UpstreamInvalidRequest("bad request")

    with pytest.raises(UpstreamInvalidRequest):
        await walk_fallback([PRIMARY, BACKUP], attempt)
    assert attempted == [PRIMARY]


async def test_exhausting_the_plan_raises_all_providers_failed() -> None:
    async def attempt(target: RoutingTarget) -> str:
        raise ProviderTimeout("down")

    with pytest.raises(AllProvidersFailed):
        await walk_fallback([PRIMARY, BACKUP], attempt)
