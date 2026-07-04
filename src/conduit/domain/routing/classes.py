"""Model class / alias resolution (pure).

Turns a requested ``model`` into an ordered candidate set:

- A **concrete** provider model (present in the route map) resolves to *itself*,
  routed to its provider — deterministic and identical to Phase 1 (back-compat).
- A **logical class/alias** (e.g. ``fast``, ``frontier``) resolves to its
  configured, ordered candidate set spanning providers.
- Anything else raises ``ModelNotFound`` (the same OpenAI-shaped 404 as Phase 1).

Aliases come from configuration, never hardcoded. Concrete-model resolution is
checked first, so an alias can never change the meaning of a real model name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.strategy import RoutingTarget


class ModelResolver:
    """Resolves a requested model into ordered ``(provider, model)`` candidates."""

    def __init__(
        self,
        routes: Mapping[str, str],
        aliases: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self._routes = dict(routes)
        self._aliases = {name: list(models) for name, models in (aliases or {}).items()}

    def resolve(self, model: str) -> list[RoutingTarget]:
        # Concrete model wins — identical to Phase 1's static mapping.
        if model in self._routes:
            return [RoutingTarget(provider=self._routes[model], model=model)]

        if model in self._aliases:
            candidates = [
                RoutingTarget(provider=self._routes[m], model=m)
                for m in self._aliases[model]
                if m in self._routes
            ]
            if candidates:
                return candidates

        raise ModelNotFound(f"no provider is configured for model {model!r}", param="model")
