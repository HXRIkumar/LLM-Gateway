"""Routing strategies.

The MVP ships :class:`StaticStrategy` — an explicit ``model → provider`` map.
This is the seam the V3 engine fills with cost/latency/capability-aware
strategies behind the same :class:`RoutingStrategy` interface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.strategy import RoutingDecision, RoutingTarget
from conduit.domain.schemas import ChatCompletionRequest


class StaticStrategy:
    """Route by an explicit ``model → provider`` map from configuration.

    An optional ``fallbacks`` map (requested model → ordered fallback model ids)
    populates the decision's fallback plan; each fallback model is resolved to its
    provider via the same route map, so the reliability layer can walk it.
    """

    def __init__(
        self,
        model_to_provider: Mapping[str, str],
        *,
        default_provider: str | None = None,
        fallbacks: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self._routes = dict(model_to_provider)
        self._default_provider = default_provider
        self._fallbacks = {k: list(v) for k, v in (fallbacks or {}).items()}

    def route(self, request: ChatCompletionRequest) -> RoutingDecision:
        provider = self._routes.get(request.model, self._default_provider)
        if provider is None:
            raise ModelNotFound(
                f"no provider is configured for model {request.model!r}", param="model"
            )
        plan: list[RoutingTarget] = []
        for fallback_model in self._fallbacks.get(request.model, ()):
            fallback_provider = self._routes.get(fallback_model)
            if fallback_provider is not None:
                plan.append(RoutingTarget(provider=fallback_provider, model=fallback_model))
        return RoutingDecision(
            provider=provider,
            model=request.model,
            reason=f"static route: model {request.model!r} -> provider {provider!r}",
            fallbacks=tuple(plan),
        )
