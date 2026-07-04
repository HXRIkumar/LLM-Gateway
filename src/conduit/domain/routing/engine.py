"""Routing strategies and the smart routing engine.

``StaticStrategy`` (the explicit ``model → provider`` map) is the default and is
used unchanged for any request that names a concrete model — guaranteeing Phase 1
back-compat. ``SmartRouter`` adds cost/latency/balanced selection for *aliases*
only, behind the same ``RoutingDecision`` output the reliability layer consumes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.catalog import Candidate, Catalog, Requirements
from conduit.domain.routing.classes import ModelResolver
from conduit.domain.routing.policy import Policy
from conduit.domain.routing.stats import LatencySnapshot
from conduit.domain.routing.strategies.balanced import BalancedWeights, rank_balanced
from conduit.domain.routing.strategies.cost import rank_by_cost
from conduit.domain.routing.strategies.latency import rank_by_latency
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

    def handles(self, model: str) -> bool:
        """True when the requested model is a concrete, statically-routed model."""
        return model in self._routes

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


def _rank_for_objective(
    objective: str,
    candidates: Sequence[Candidate],
    request: ChatCompletionRequest,
    snapshot: LatencySnapshot,
    weights: BalancedWeights,
) -> list[Candidate]:
    if objective == "cost":
        return rank_by_cost(candidates, request)
    if objective == "latency":
        return rank_by_latency(candidates, snapshot)
    return rank_balanced(candidates, request, snapshot, weights)


class SmartRouter:
    """Selects a route: static for concrete models, objective-ranked for aliases."""

    def __init__(
        self,
        static: StaticStrategy,
        resolver: ModelResolver,
        catalog: Catalog,
        weights: BalancedWeights | None = None,
    ) -> None:
        self._static = static
        self._resolver = resolver
        self._catalog = catalog
        self._weights = weights or BalancedWeights()

    def route(
        self,
        request: ChatCompletionRequest,
        requirements: Requirements,
        policy: Policy,
        snapshot: LatencySnapshot,
    ) -> RoutingDecision:
        # Concrete model → static, byte-for-byte Phase 1/2 (incl. config fallbacks).
        if self._static.handles(request.model):
            return self._static.route(request)

        # Alias/class → capability + policy filtering, then rank by the objective.
        targets = self._resolver.resolve(request.model)  # ModelNotFound if truly unknown
        candidates = [
            found
            for target in targets
            if (found := self._catalog.find(target.provider, target.model)) is not None
        ]
        allowed = [c for c in candidates if policy.permits(c.provider)]
        capable = Catalog(allowed).capable(requirements)
        if not capable:
            raise ModelNotFound(
                f"no capable, permitted provider for model {request.model!r}", param="model"
            )
        ranked = _rank_for_objective(policy.objective, capable, request, snapshot, self._weights)
        primary, *rest = ranked
        return RoutingDecision(
            provider=primary.provider,
            model=primary.model.id,
            reason=(
                f"{policy.objective} route: alias {request.model!r} -> "
                f"{primary.provider}:{primary.model.id}"
            ),
            fallbacks=tuple(RoutingTarget(provider=c.provider, model=c.model.id) for c in rest),
        )
