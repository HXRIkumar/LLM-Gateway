"""Unit tests for the pure cost computation."""

from __future__ import annotations

from decimal import Decimal

from conduit.providers.base import ModelPricing
from conduit.services.usage import compute_cost


def test_cost_from_per_1k_pricing() -> None:
    pricing = ModelPricing(input_per_1k_usd=0.005, output_per_1k_usd=0.015)
    # 1000/1000 * 0.005 + 500/1000 * 0.015 = 0.005 + 0.0075
    assert compute_cost(pricing, 1000, 500) == Decimal("0.0125")


def test_cost_is_zero_for_free_models() -> None:
    assert compute_cost(ModelPricing(), 12345, 6789) == Decimal("0")


def test_cost_scales_with_tokens() -> None:
    pricing = ModelPricing(input_per_1k_usd=0.001, output_per_1k_usd=0.002)
    assert compute_cost(pricing, 2000, 0) == Decimal("0.002")
    assert compute_cost(pricing, 0, 3000) == Decimal("0.006")
