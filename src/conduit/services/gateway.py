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
from dataclasses import dataclass, field
from decimal import Decimal

import structlog
from opentelemetry.trace import Tracer

from conduit.domain.errors import (
    AllProvidersFailed,
    BudgetExceeded,
    ConduitError,
    ProviderError,
    RateLimited,
)
from conduit.domain.reliability.breaker import CircuitBreaker
from conduit.domain.reliability.fallback import walk_fallback
from conduit.domain.reliability.ratelimit import RateLimit, RateLimiter
from conduit.domain.reliability.retry import RetryPolicy, is_retryable, retry_async
from conduit.domain.routing.classify import classify
from conduit.domain.routing.engine import SmartRouter
from conduit.domain.routing.policy import DEFAULT_POLICY, Policy
from conduit.domain.routing.stats import LatencyStats
from conduit.domain.routing.strategy import RoutingDecision, RoutingTarget
from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Usage,
)
from conduit.infra.telemetry.tracing import get_tracer
from conduit.providers.base import Provider
from conduit.providers.registry import ProviderRegistry
from conduit.services.budgets import BudgetService
from conduit.services.keys import Principal
from conduit.services.policies import PolicyService
from conduit.services.usage import UsageService

logger = structlog.get_logger("conduit.gateway")


@dataclass
class _Observation:
    """Mutable per-request execution facts gathered for the access log + metrics."""

    attempts: int = 0
    providers_tried: list[str] = field(default_factory=list)
    breaker_outcome: str = "closed"


class Gateway:
    """Orchestrates the request pipeline over routing + provider adapters."""

    def __init__(
        self,
        registry: ProviderRegistry,
        router: SmartRouter,
        usage: UsageService,
        *,
        rate_limiter: RateLimiter | None = None,
        key_limit: RateLimit | None = None,
        org_limit: RateLimit | None = None,
        budget: BudgetService | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
        breaker: CircuitBreaker | None = None,
        policy_service: PolicyService | None = None,
        stats: LatencyStats | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._usage = usage
        self._tracer = tracer or get_tracer()
        self._rate_limiter = rate_limiter
        self._key_limit = key_limit
        self._org_limit = org_limit
        self._budget = budget
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep or asyncio.sleep
        self._rng = rng or random.Random()
        self._breaker = breaker
        self._policy_service = policy_service
        self._stats = stats

    async def chat_completion(
        self, request: ChatCompletionRequest, principal: Principal
    ) -> ChatCompletionResponse:
        with self._tracer.start_as_current_span("gateway.chat_completion") as span:
            span.set_attribute("conduit.request_model", request.model)
            decision = await self._plan(request, principal)
            obs = _Observation()
            start = time.perf_counter()
            response, target = await self._execute_with_fallback(request, decision, obs)
            latency_ms = int((time.perf_counter() - start) * 1000)
            cost = await self._account(
                target.provider,
                target.model,
                principal,
                usage=response.usage,
                latency_ms=latency_ms,
                status="ok",
            )
            span.set_attribute("conduit.provider", target.provider)
            span.set_attribute("conduit.model", target.model)
            span.set_attribute("conduit.attempts", obs.attempts)
            span.set_attribute(
                "conduit.total_tokens", response.usage.total_tokens if response.usage else 0
            )
            span.set_attribute("conduit.status", "ok")
            self._log_completion(
                principal, decision, target, response.usage, cost, latency_ms, obs, "ok"
            )
            return response

    async def _execute_with_fallback(
        self, request: ChatCompletionRequest, decision: RoutingDecision, obs: _Observation
    ) -> tuple[ChatCompletionResponse, RoutingTarget]:
        """Try the primary then each fallback (breaker + retry per target)."""
        targets = [
            RoutingTarget(provider=decision.provider, model=decision.model),
            *decision.fallbacks,
        ]

        async def attempt(target: RoutingTarget) -> ChatCompletionResponse:
            obs.providers_tried.append(target.provider)
            provider = self._registry.get(target.provider)
            return await self._execute_unary(provider, self._for_target(request, target), obs)

        return await walk_fallback(targets, attempt)

    @staticmethod
    def _for_target(request: ChatCompletionRequest, target: RoutingTarget) -> ChatCompletionRequest:
        if target.model == request.model:
            return request
        return request.model_copy(update={"model": target.model})

    async def _execute_unary(
        self, provider: Provider, request: ChatCompletionRequest, obs: _Observation
    ) -> ChatCompletionResponse:
        """Execute a unary call under the circuit breaker + retry policy.

        An open breaker fast-fails without calling the provider. Otherwise the
        call runs under bounded retry; a transient failure of the whole attempt
        records one breaker failure, a success closes it.
        """
        breaker = self._breaker
        if breaker is not None and not await breaker.allow(provider.name):
            obs.breaker_outcome = "open"
            raise ProviderError(f"circuit breaker open for provider {provider.name!r}")

        async def call() -> ChatCompletionResponse:
            obs.attempts += 1
            with self._tracer.start_as_current_span("provider.request") as span:
                span.set_attribute("conduit.provider", provider.name)
                span.set_attribute("conduit.attempt", obs.attempts)
                return await provider.chat_completion(request)

        try:
            result = await retry_async(call, self._retry_policy, sleep=self._sleep, rng=self._rng)
        except ConduitError as exc:
            if breaker is not None and is_retryable(exc):
                await breaker.record_failure(provider.name)
                obs.breaker_outcome = "failure"
            raise
        if breaker is not None:
            await breaker.record_success(provider.name)
        return result

    async def stream_chat_completion(
        self, request: ChatCompletionRequest, principal: Principal
    ) -> AsyncIterator[ChatCompletionChunk]:
        decision = await self._plan(request, principal)
        targets = [
            RoutingTarget(provider=decision.provider, model=decision.model),
            *decision.fallbacks,
        ]
        obs = _Observation()
        start = time.perf_counter()
        with self._tracer.start_as_current_span("gateway.stream_chat_completion") as span:
            span.set_attribute("conduit.request_model", request.model)
            iterator, target, first_chunk = await self._open_stream(request, targets, obs)
            span.set_attribute("conduit.provider", target.provider)
            span.set_attribute("conduit.model", target.model)
            span.set_attribute("conduit.attempts", obs.attempts)
        last_usage: Usage | None = first_chunk.usage
        yield first_chunk
        async for chunk in iterator:
            if chunk.usage is not None:
                last_usage = chunk.usage
            yield chunk
        # Reached only on full, successful completion (after [DONE]).
        latency_ms = int((time.perf_counter() - start) * 1000)
        cost = await self._account(
            target.provider,
            target.model,
            principal,
            usage=last_usage,
            latency_ms=latency_ms,
            status="ok",
        )
        self._log_completion(principal, decision, target, last_usage, cost, latency_ms, obs, "ok")

    async def _open_stream(
        self, request: ChatCompletionRequest, targets: list[RoutingTarget], obs: _Observation
    ) -> tuple[AsyncIterator[ChatCompletionChunk], RoutingTarget, ChatCompletionChunk]:
        """Prime a stream, falling back before the first byte. Never retried mid-stream."""
        last: ConduitError | None = None
        for target in targets:
            obs.providers_tried.append(target.provider)
            obs.attempts += 1
            provider = self._registry.get(target.provider)
            if self._breaker is not None and not await self._breaker.allow(provider.name):
                obs.breaker_outcome = "open"
                last = ProviderError(f"circuit breaker open for provider {provider.name!r}")
                continue
            span = self._tracer.start_span("provider.request")
            span.set_attribute("conduit.provider", provider.name)
            iterator = provider.stream_chat_completion(
                self._for_target(request, target)
            ).__aiter__()
            try:
                first_chunk = await iterator.__anext__()
            except StopAsyncIteration:
                span.end()
                last = ProviderError(f"provider {provider.name!r} returned an empty stream")
                if self._breaker is not None:
                    await self._breaker.record_failure(provider.name)
                    obs.breaker_outcome = "failure"
                continue
            except ConduitError as exc:
                span.end()
                if not is_retryable(exc):
                    raise
                if self._breaker is not None:
                    await self._breaker.record_failure(provider.name)
                    obs.breaker_outcome = "failure"
                last = exc
                continue
            span.end()
            if self._breaker is not None:
                await self._breaker.record_success(provider.name)
            return iterator, target, first_chunk
        raise AllProvidersFailed("all providers in the routing plan failed") from last

    # --- stages -------------------------------------------------------------

    async def _plan(self, request: ChatCompletionRequest, principal: Principal) -> RoutingDecision:
        """Shared preflight + routing for both unary and streaming paths."""
        await self._preflight(request, principal)
        requirements = classify(request)  # classification seam (§5 step 3)

        with self._tracer.start_as_current_span("gateway.route") as span:
            # Concrete models take the static fast path — no policy/stats I/O, no
            # behaviour change from Phase 1/2. Only aliases run smart routing.
            if self._router.is_static(request.model):
                decision = self._router.route(request, requirements, DEFAULT_POLICY, {})
                objective = "static"
            else:
                policy = await self._load_policy(principal)
                snapshot = await self._latency_snapshot()
                decision = self._router.route(request, requirements, policy, snapshot)
                objective = policy.objective
            span.set_attribute("conduit.objective", objective)
            span.set_attribute("conduit.provider", decision.provider)
            span.set_attribute("conduit.model", decision.model)

        logger.info(
            "routed request",
            provider=decision.provider,
            model=decision.model,
            reason=decision.reason,
            key_prefix=principal.prefix,
            objective=objective,
            needs_tools=requirements.needs_tools,
            needs_vision=requirements.needs_vision,
            min_context=requirements.min_context,
        )
        return decision

    async def _load_policy(self, principal: Principal) -> Policy:
        if self._policy_service is None:
            return DEFAULT_POLICY
        return await self._policy_service.effective(
            org_id=principal.org_id, api_key_id=principal.api_key_id
        )

    async def _latency_snapshot(self) -> dict[tuple[str, str], float]:
        if self._stats is None:
            return {}
        return await self._stats.snapshot(self._router.catalog_targets())

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
        provider: str,
        model: str,
        principal: Principal,
        *,
        usage: Usage | None,
        latency_ms: int,
        status: str,
    ) -> Decimal:
        return await self._usage.record(
            org_id=principal.org_id,
            api_key_id=principal.api_key_id,
            provider=provider,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            status=status,
        )

    def _log_completion(
        self,
        principal: Principal,
        decision: RoutingDecision,
        target: RoutingTarget,
        usage: Usage | None,
        cost: Decimal,
        latency_ms: int,
        obs: _Observation,
        status: str,
    ) -> None:
        """One complete, correlated access log per request. No secrets/bodies (§7)."""
        logger.info(
            "request.completed",
            key_prefix=principal.prefix,
            provider=target.provider,
            model=target.model,
            reason=decision.reason,
            attempts=obs.attempts,
            providers_tried=obs.providers_tried,
            breaker_outcome=obs.breaker_outcome,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            cost_usd=str(cost),
            latency_ms=latency_ms,
            status=status,
        )
