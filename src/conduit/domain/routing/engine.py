"""Routing strategies.

The MVP ships :class:`StaticStrategy` — an explicit ``model → provider`` map.
This is the seam the V3 engine fills with cost/latency/capability-aware
strategies behind the same :class:`RoutingStrategy` interface.
"""

from __future__ import annotations

from collections.abc import Mapping

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.strategy import RoutingDecision
from conduit.domain.schemas import ChatCompletionRequest


class StaticStrategy:
    """Route by an explicit ``model → provider`` map from configuration."""

    def __init__(
        self,
        model_to_provider: Mapping[str, str],
        *,
        default_provider: str | None = None,
    ) -> None:
        self._routes = dict(model_to_provider)
        self._default_provider = default_provider

    def route(self, request: ChatCompletionRequest) -> RoutingDecision:
        provider = self._routes.get(request.model, self._default_provider)
        if provider is None:
            raise ModelNotFound(
                f"no provider is configured for model {request.model!r}", param="model"
            )
        return RoutingDecision(
            provider=provider,
            model=request.model,
            reason=f"static route: model {request.model!r} -> provider {provider!r}",
        )
