"""The request pipeline — the heart of the gateway (CLAUDE.md §5).

    authenticate → validate → preflight → route → execute → account → respond

Authentication and validation happen at the edge and arrive as a ``Principal``
and a validated request. Preflight enforces rate limits (Task 2) and budgets
(Task 3); execute is hardened with retry/breaker/fallback (Tasks 4-6); account
persists usage (Task 1). Reliability collaborators are optional — when absent the
pipeline degrades to the Phase 1 behaviour, which keeps the domain testable.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import structlog

from conduit.domain.errors import BudgetExceeded, RateLimited
from conduit.domain.reliability.ratelimit import RateLimit, RateLimiter
from conduit.domain.reliability.retry import RetryPolicy, retry_async
from conduit.domain.routing.strategy import RoutingDecision, RoutingStrategy
from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Usage,
)
from conduit.providers.base import Provider
from conduit.providers.registry import ProviderRegistry
from conduit.services.budgets import BudgetService
from conduit.services.keys import Principal
from conduit.services.usage import UsageService

logger = structlog.get_logger("conduit.gateway")


class Gateway:
    """Orchestrates the request pipeline over routing + provider adapters."""

    def __init__(
        self,
        registry: ProviderRegistry,
        routing: RoutingStrategy,
        usage: UsageService,
        *,
        rate_limiter: RateLimiter | None = None,
        key_limit: RateLimit | None = None,
        org_limit: RateLimit | None = None,
        budget: BudgetService | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._registry = registry
        self._routing = routing
        self._usage = usage
        self._rate_limiter = rate_limiter
        self._key_limit = key_limit
        self._org_limit = org_limit
        self._budget = budget
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep or asyncio.sleep
        self._rng = rng or random.Random()

    async def chat_completion(
        self, request: ChatCompletionRequest, principal: Principal
    ) -> ChatCompletionResponse:
        decision = await self._plan(request, principal)
        provider = self._registry.get(decision.provider)
        start = time.perf_counter()
        response = await self._execute_unary(provider, request)
        latency_ms = int((time.perf_counter() - start) * 1000)
        await self._account(
            decision, principal, usage=response.usage, latency_ms=latency_ms, status="ok"
        )
        return response

    async def _execute_unary(
        self, provider: Provider, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """Execute a unary call under the retry policy (same provider, bounded)."""

        async def call() -> ChatCompletionResponse:
            return await provider.chat_completion(request)

        return await retry_async(call, self._retry_policy, sleep=self._sleep, rng=self._rng)

    async def stream_chat_completion(
        self, request: ChatCompletionRequest, principal: Principal
    ) -> AsyncIterator[ChatCompletionChunk]:
        decision = await self._plan(request, principal)
        provider = self._registry.get(decision.provider)
        start = time.perf_counter()
        last_usage: Usage | None = None
        async for chunk in provider.stream_chat_completion(request):
            if chunk.usage is not None:
                last_usage = chunk.usage
            yield chunk
        # Reached only on full, successful completion (after [DONE]).
        latency_ms = int((time.perf_counter() - start) * 1000)
        await self._account(
            decision, principal, usage=last_usage, latency_ms=latency_ms, status="ok"
        )

    # --- stages -------------------------------------------------------------

    async def _plan(self, request: ChatCompletionRequest, principal: Principal) -> RoutingDecision:
        """Shared preflight + routing for both unary and streaming paths."""
        await self._preflight(request, principal)
        decision = self._routing.route(request)
        logger.info(
            "routed request",
            provider=decision.provider,
            model=decision.model,
            reason=decision.reason,
            key_prefix=principal.prefix,
        )
        return decision

    async def _preflight(self, request: ChatCompletionRequest, principal: Principal) -> None:
        """Preflight policy: rate limits then budget."""
        await self._check_rate_limits(principal)
        await self._check_budget(principal)

    async def _check_budget(self, principal: Principal) -> None:
        if self._budget is None:
            return
        decision = await self._budget.check(principal.org_id)
        if decision is not None and not decision.allowed:
            raise BudgetExceeded("budget exceeded for the current period")

    async def _check_rate_limits(self, principal: Principal) -> None:
        if self._rate_limiter is None:
            return
        scoped: list[tuple[str, RateLimit]] = []
        if self._key_limit is not None:
            scoped.append((f"key:{principal.api_key_id}", self._key_limit))
        if self._org_limit is not None:
            scoped.append((f"org:{principal.org_id}", self._org_limit))
        for scope, limit in scoped:
            result = await self._rate_limiter.check(scope, limit)
            if not result.allowed:
                raise RateLimited("rate limit exceeded", retry_after=result.retry_after_seconds)

    async def _account(
        self,
        decision: RoutingDecision,
        principal: Principal,
        *,
        usage: Usage | None,
        latency_ms: int,
        status: str,
    ) -> None:
        await self._usage.record(
            org_id=principal.org_id,
            api_key_id=principal.api_key_id,
            provider=decision.provider,
            model=decision.model,
            usage=usage,
            latency_ms=latency_ms,
            status=status,
        )
