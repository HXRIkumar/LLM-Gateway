"""Fallback walking (pure): try each target in the routing plan in turn.

On a *retryable* failure (transient upstream error or an open breaker) advance to
the next target; a *terminal* failure (a bad request) is the caller's fault and
is raised immediately without falling back. Exhausting the plan raises
``AllProvidersFailed``. The per-target work (breaker, retry, the provider call)
is supplied by ``attempt`` — this module only decides fall-through.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from conduit.domain.errors import AllProvidersFailed, ConduitError
from conduit.domain.reliability.retry import is_retryable
from conduit.domain.routing.strategy import RoutingTarget


async def walk_fallback[T](
    targets: list[RoutingTarget],
    attempt: Callable[[RoutingTarget], Awaitable[T]],
) -> tuple[T, RoutingTarget]:
    """Try each target until one succeeds; return (result, the target that served it)."""
    last: ConduitError | None = None
    for target in targets:
        try:
            result = await attempt(target)
        except ConduitError as exc:
            if not is_retryable(exc):
                raise  # terminal (e.g. bad request) — another provider won't help
            last = exc
            continue
        return result, target
    raise AllProvidersFailed("all providers in the routing plan failed") from last
