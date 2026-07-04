"""Unit tests for cost-optimized ranking."""

from __future__ import annotations

from decimal import Decimal

from conduit.domain.routing.catalog import Candidate
from conduit.domain.routing.strategies.cost import estimate_cost, rank_by_cost
from conduit.domain.schemas import ChatCompletionRequest
from conduit.providers.base import ModelInfo, ModelPricing


def _candidate(provider: str, model_id: str, inp: float, out: float) -> Candidate:
    return Candidate(
        provider=provider,
        model=ModelInfo(
            id=model_id,
            context_window=128_000,
            pricing=ModelPricing(input_per_1k_usd=inp, output_per_1k_usd=out),
        ),
    )


def _request() -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {"model": "x", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1000}
    )


def test_cheapest_capable_candidate_ranked_first() -> None:
    pricey = _candidate("openai", "gpt-4o", 0.005, 0.015)
    cheap = _candidate("openai", "gpt-4o-mini", 0.00015, 0.0006)
    free = _candidate("ollama", "llama3.2", 0.0, 0.0)
    ranked = rank_by_cost([pricey, cheap, free], _request())
    assert [c.model.id for c in ranked] == ["llama3.2", "gpt-4o-mini", "gpt-4o"]


def test_estimate_cost_matches_formula() -> None:
    pricing = ModelPricing(input_per_1k_usd=0.005, output_per_1k_usd=0.015)
    # 1000/1000*0.005 + 500/1000*0.015
    assert estimate_cost(pricing, 1000, 500) == Decimal("0.0125")


def test_ties_break_deterministically_by_input_order() -> None:
    a = _candidate("p1", "m-a", 0.001, 0.001)
    b = _candidate("p2", "m-b", 0.001, 0.001)
    assert [c.model.id for c in rank_by_cost([a, b], _request())] == ["m-a", "m-b"]
    assert [c.model.id for c in rank_by_cost([b, a], _request())] == ["m-b", "m-a"]
