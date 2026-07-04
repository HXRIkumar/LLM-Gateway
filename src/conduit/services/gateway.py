"""The request pipeline — the heart of the gateway (CLAUDE.md §5).

    authenticate → validate → preflight → route → execute → account → respond

Authentication and validation happen at the edge and arrive as a ``Principal``
and a validated request. Preflight (rate limit + budget) is a seam filled in
Phase 2 Tasks 2-3; execute is hardened with retry/breaker/fallback in Tasks 4-6;
account persists usage here (Task 1). Accounting runs once per completed request
— for unary from the response, for streaming after the stream drains.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import structlog

from conduit.domain.routing.strategy import RoutingDecision, RoutingStrategy
from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Usage,
)
from conduit.providers.registry import ProviderRegistry
from conduit.services.keys import Principal
from conduit.services.usage import UsageService

logger = structlog.get_logger("conduit.gateway")


class Gateway:
    """Orchestrates the request pipeline over routing + provider adapters."""

    def __init__(
        self, registry: ProviderRegistry, routing: RoutingStrategy, usage: UsageService
    ) -> None:
        self._registry = registry
        self._routing = routing
        self._usage = usage

    async def chat_completion(
        self, request: ChatCompletionRequest, principal: Principal
    ) -> ChatCompletionResponse:
        decision = self._plan(request, principal)
        provider = self._registry.get(decision.provider)
        start = time.perf_counter()
        response = await provider.chat_completion(request)
        latency_ms = int((time.perf_counter() - start) * 1000)
        await self._account(
            decision, principal, usage=response.usage, latency_ms=latency_ms, status="ok"
        )
        return response

    async def stream_chat_completion(
        self, request: ChatCompletionRequest, principal: Principal
    ) -> AsyncIterator[ChatCompletionChunk]:
        decision = self._plan(request, principal)
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

    def _plan(self, request: ChatCompletionRequest, principal: Principal) -> RoutingDecision:
        """Shared preflight + routing for both unary and streaming paths."""
        self._preflight(request, principal)
        decision = self._routing.route(request)
        logger.info(
            "routed request",
            provider=decision.provider,
            model=decision.model,
            reason=decision.reason,
            key_prefix=principal.prefix,
        )
        return decision

    def _preflight(self, request: ChatCompletionRequest, principal: Principal) -> None:
        """Preflight-policy seam: rate limit + budget (Phase 2 Tasks 2-3)."""

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
