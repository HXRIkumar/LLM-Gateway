"""The request pipeline — the heart of the gateway (CLAUDE.md §5).

Every request flows through the same ordered stages:

    authenticate → validate → preflight → route → execute → account → respond

Authentication and validation happen at the edge (the auth dependency and
Pydantic parsing) and arrive here as a resolved ``Principal`` and a validated
``ChatCompletionRequest``. Preflight (budget/limits, V2) and accounting
(usage/cost + decision recording, V2) are real seams here — no-ops today, filled
in later phases without reshaping the pipeline.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from conduit.domain.routing.strategy import RoutingDecision, RoutingStrategy
from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from conduit.providers.registry import ProviderRegistry
from conduit.services.keys import Principal

logger = structlog.get_logger("conduit.gateway")


class Gateway:
    """Orchestrates the request pipeline over routing + provider adapters."""

    def __init__(self, registry: ProviderRegistry, routing: RoutingStrategy) -> None:
        self._registry = registry
        self._routing = routing

    async def chat_completion(
        self, request: ChatCompletionRequest, principal: Principal
    ) -> ChatCompletionResponse:
        decision = self._plan(request, principal)
        provider = self._registry.get(decision.provider)
        response = await provider.chat_completion(request)
        self._account(request, decision, principal)
        return response

    async def stream_chat_completion(
        self, request: ChatCompletionRequest, principal: Principal
    ) -> AsyncIterator[ChatCompletionChunk]:
        decision = self._plan(request, principal)
        provider = self._registry.get(decision.provider)
        async for chunk in provider.stream_chat_completion(request):
            yield chunk
        self._account(request, decision, principal)

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
        """Preflight-policy seam: budget + rate limit (V2), classification (V3)."""

    def _account(
        self, request: ChatCompletionRequest, decision: RoutingDecision, principal: Principal
    ) -> None:
        """Usage/cost accounting + decision recording seam (V2)."""
