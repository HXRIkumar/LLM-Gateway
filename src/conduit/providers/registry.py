"""Provider registry: ``name → provider``.

Adapters are constructed once (in the app factory) from a table of factories and
the shared httpx client. ``BUILTIN_PROVIDER_FACTORIES`` is the single place a new
built-in provider is registered — the table is a module-level constant, not
mutable runtime state, and it is overridable in tests via ``build_registry``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import httpx

from conduit.config import Settings
from conduit.domain.errors import ProviderError
from conduit.providers.base import ModelInfo, Provider
from conduit.providers.ollama import build_ollama_provider
from conduit.providers.openai import build_openai_provider

# A factory builds a provider from settings and the shared outbound HTTP client.
ProviderFactory = Callable[[Settings, httpx.AsyncClient], Provider]

# Built-in providers. Adding one = implement the adapter and add a single entry
# here (ADR-0003).
BUILTIN_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    "openai": build_openai_provider,
    "ollama": build_ollama_provider,
}


class ProviderRegistry:
    """Holds the constructed providers and resolves them by name."""

    def __init__(self, providers: Mapping[str, Provider]) -> None:
        self._providers: dict[str, Provider] = dict(providers)

    def get(self, name: str) -> Provider:
        try:
            return self._providers[name]
        except KeyError:
            # A routing decision pointing at an unregistered provider is an
            # internal misconfiguration, not a client error.
            raise ProviderError(f"provider {name!r} is not registered") from None

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    def names(self) -> list[str]:
        return sorted(self._providers)

    def all_models(self) -> list[ModelInfo]:
        """Every advertised model across all providers (for ``GET /v1/models``)."""
        return [model for name in self.names() for model in self._providers[name].models]

    def model_provider_map(self) -> dict[str, str]:
        """Map each advertised model id to the provider that serves it."""
        return {model.id: name for name in self.names() for model in self._providers[name].models}


def build_registry(
    settings: Settings,
    http_client: httpx.AsyncClient,
    factories: Mapping[str, ProviderFactory] | None = None,
) -> ProviderRegistry:
    """Construct all registered providers into a registry."""
    factories = BUILTIN_PROVIDER_FACTORIES if factories is None else factories
    return ProviderRegistry(
        {name: factory(settings, http_client) for name, factory in factories.items()}
    )
