"""Unit tests for balanced (weighted cost+latency) ranking."""

from __future__ import annotations

from conduit.domain.routing.catalog import Candidate
from conduit.domain.routing.strategies.balanced import BalancedWeights, rank_balanced
from conduit.domain.schemas import ChatCompletionRequest
from conduit.providers.base import ModelInfo, ModelPricing


def _candidate(model_id: str, out_price: float) -> Candidate:
    return Candidate(
        provider=model_id,
        model=ModelInfo(
            id=model_id,
            context_window=128_000,
            pricing=ModelPricing(input_per_1k_usd=0.0, output_per_1k_usd=out_price),
        ),
    )


# A: cheapest but slowest; B: priciest but fastest; C: middle on both.
A = _candidate("A", 0.001)
B = _candidate("B", 0.010)
C = _candidate("C", 0.004)
SNAPSHOT = {("A", "A"): 500.0, ("B", "B"): 100.0, ("C", "C"): 300.0}


def _request() -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {"model": "x", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1000}
    )


def test_cost_weight_orders_by_cost() -> None:
    ranked = rank_balanced([A, B, C], _request(), SNAPSHOT, BalancedWeights(cost=1.0, latency=0.0))
    assert [c.model.id for c in ranked] == ["A", "C", "B"]


def test_latency_weight_orders_by_latency() -> None:
    ranked = rank_balanced([A, B, C], _request(), SNAPSHOT, BalancedWeights(cost=0.0, latency=1.0))
    assert [c.model.id for c in ranked] == ["B", "C", "A"]


def test_balanced_picks_the_compromise() -> None:
    ranked = rank_balanced([A, B, C], _request(), SNAPSHOT, BalancedWeights(cost=0.5, latency=0.5))
    # C is neither cheapest nor fastest but the best blend of the two.
    assert ranked[0].model.id == "C"


def test_error_rate_deweights_a_degrading_provider() -> None:
    weights = BalancedWeights(cost=1.0, latency=0.0, error=1.0)
    # Cost alone ranks A first. A climbing error rate on A demotes it below C.
    ranked = rank_balanced([A, B, C], _request(), SNAPSHOT, weights, error_rates={("A", "A"): 0.9})
    assert ranked[0].model.id != "A"
    assert "A" in [c.model.id for c in ranked]  # de-weighted, not dropped


def test_empty_error_rates_leaves_ranking_unchanged() -> None:
    weights = BalancedWeights(cost=1.0, latency=0.0, error=1.0)
    ranked = rank_balanced([A, B, C], _request(), SNAPSHOT, weights, error_rates={})
    assert [c.model.id for c in ranked] == ["A", "C", "B"]
