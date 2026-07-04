"""Unit tests for the pure cost predictor."""

from __future__ import annotations

from decimal import Decimal

from conduit.domain.optimize.predict import (
    DEFAULT_COMPLETION_TOKENS,
    estimate_completion_tokens,
    estimate_cost,
    estimate_prompt_tokens,
)
from conduit.domain.schemas import ChatCompletionRequest
from conduit.providers.base import ModelPricing


def _req(**overrides: object) -> ChatCompletionRequest:
    base: dict = {"model": "m", "messages": [{"role": "user", "content": "hello there"}]}
    base.update(overrides)
    return ChatCompletionRequest.model_validate(base)


def test_prompt_tokens_scale_with_text_length() -> None:
    short = estimate_prompt_tokens(_req(messages=[{"role": "user", "content": "hi"}]))
    long = estimate_prompt_tokens(_req(messages=[{"role": "user", "content": "x" * 400}]))
    assert long > short >= 1


def test_completion_tokens_prefer_explicit_cap() -> None:
    assert estimate_completion_tokens(_req(max_tokens=42)) == 42
    assert estimate_completion_tokens(_req(max_completion_tokens=7)) == 7
    assert estimate_completion_tokens(_req()) == DEFAULT_COMPLETION_TOKENS


def test_estimate_cost_matches_per_1k_pricing() -> None:
    pricing = ModelPricing(input_per_1k_usd=0.001, output_per_1k_usd=0.002)
    # 1000 prompt + 500 completion → 0.001 + 0.001 = 0.002
    assert estimate_cost(pricing, 1000, 500) == Decimal("0.002")


def test_estimate_cost_zero_when_no_pricing() -> None:
    assert estimate_cost(ModelPricing(), 1000, 1000) == Decimal("0")
