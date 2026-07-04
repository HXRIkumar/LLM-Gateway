"""Unit tests for SmartRouter: concrete bypass + objective-based alias routing."""

from __future__ import annotations

import pytest

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.catalog import Candidate, Catalog, Requirements
from conduit.domain.routing.classes import ModelResolver
from conduit.domain.routing.engine import SmartRouter, StaticStrategy
from conduit.domain.routing.policy import Policy
from conduit.domain.schemas import ChatCompletionRequest
from conduit.providers.base import ModelInfo, ModelPricing

ROUTES = {"gpt-4o-mini": "openai", "gpt-4o": "openai", "llama3.2": "ollama"}
ALIASES = {"fast": ["gpt-4o-mini", "llama3.2"]}

CATALOG = Catalog(
    [
        Candidate(
            provider="openai",
            model=ModelInfo(
                id="gpt-4o-mini",
                context_window=128_000,
                supports_tools=True,
                supports_json_mode=True,
                supports_vision=True,
                pricing=ModelPricing(input_per_1k_usd=0.00015, output_per_1k_usd=0.0006),
            ),
        ),
        Candidate(
            provider="ollama",
            model=ModelInfo(id="llama3.2", context_window=131_072, supports_tools=True),
        ),
    ]
)


def _router() -> SmartRouter:
    static = StaticStrategy(ROUTES, fallbacks={"gpt-4o-mini": ["llama3.2"]})
    return SmartRouter(static, ModelResolver(ROUTES, ALIASES), CATALOG)


def _request(model: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=model, messages=[{"role": "user", "content": "hi"}])  # type: ignore[list-item]


@pytest.mark.parametrize("objective", ["cost", "latency", "balanced"])
def test_concrete_model_bypasses_to_static_regardless_of_objective(objective: str) -> None:
    router = _router()
    decision = router.route(
        _request("gpt-4o-mini"), Requirements(), Policy(objective=objective), {}
    )
    # Identical to Phase 1/2 static routing, including the configured fallback.
    assert decision.provider == "openai"
    assert decision.model == "gpt-4o-mini"
    assert (
        decision.fallbacks
        == StaticStrategy(ROUTES, fallbacks={"gpt-4o-mini": ["llama3.2"]})
        .route(_request("gpt-4o-mini"))
        .fallbacks
    )


def test_alias_cost_objective_picks_cheapest() -> None:
    decision = _router().route(_request("fast"), Requirements(), Policy(objective="cost"), {})
    assert (decision.provider, decision.model) == ("ollama", "llama3.2")  # free < gpt-4o-mini


def test_alias_latency_objective_picks_fastest() -> None:
    snapshot = {("openai", "gpt-4o-mini"): 100.0, ("ollama", "llama3.2"): 400.0}
    decision = _router().route(
        _request("fast"), Requirements(), Policy(objective="latency"), snapshot
    )
    assert (decision.provider, decision.model) == ("openai", "gpt-4o-mini")


def test_policy_denylist_excludes_provider() -> None:
    policy = Policy(objective="cost", deny_providers=("ollama",))
    decision = _router().route(_request("fast"), Requirements(), policy, {})
    assert decision.provider == "openai"
    assert decision.fallbacks == ()  # ollama removed, nothing left to fall back to


def test_capability_filter_excludes_incapable_candidate() -> None:
    # Vision needed → llama3.2 (no vision) excluded; only gpt-4o-mini remains.
    decision = _router().route(
        _request("fast"), Requirements(needs_vision=True), Policy(objective="cost"), {}
    )
    assert (decision.provider, decision.model) == ("openai", "gpt-4o-mini")


def test_unknown_model_raises_404() -> None:
    with pytest.raises(ModelNotFound):
        _router().route(_request("nope"), Requirements(), Policy(), {})
