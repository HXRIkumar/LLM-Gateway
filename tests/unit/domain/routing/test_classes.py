"""Unit tests for model class / alias resolution."""

from __future__ import annotations

import pytest

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.classes import ModelResolver
from conduit.domain.routing.strategy import RoutingTarget

ROUTES = {"gpt-4o-mini": "openai", "gpt-4o": "openai", "llama3.2": "ollama"}
ALIASES = {"fast": ["gpt-4o-mini", "llama3.2"], "frontier": ["gpt-4o"]}


def _resolver() -> ModelResolver:
    return ModelResolver(ROUTES, ALIASES)


def test_concrete_model_resolves_to_itself() -> None:
    # Deterministic, identical to Phase 1's static mapping.
    assert _resolver().resolve("gpt-4o-mini") == [
        RoutingTarget(provider="openai", model="gpt-4o-mini")
    ]


def test_alias_resolves_to_ordered_candidate_set() -> None:
    assert _resolver().resolve("fast") == [
        RoutingTarget(provider="openai", model="gpt-4o-mini"),
        RoutingTarget(provider="ollama", model="llama3.2"),
    ]


def test_single_member_alias() -> None:
    assert _resolver().resolve("frontier") == [RoutingTarget(provider="openai", model="gpt-4o")]


def test_unknown_model_raises_model_not_found() -> None:
    with pytest.raises(ModelNotFound) as exc_info:
        _resolver().resolve("no-such-model")
    assert exc_info.value.status_code == 404


def test_alias_skips_unrouted_members() -> None:
    resolver = ModelResolver(ROUTES, {"mix": ["gpt-4o-mini", "ghost-model", "llama3.2"]})
    assert resolver.resolve("mix") == [
        RoutingTarget(provider="openai", model="gpt-4o-mini"),
        RoutingTarget(provider="ollama", model="llama3.2"),
    ]
