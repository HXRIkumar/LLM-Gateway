"""Unit tests for the pure capability catalog."""

from __future__ import annotations

from conduit.domain.routing.catalog import Candidate, Catalog, Requirements
from conduit.providers.base import ModelInfo, ModelPricing

BIG_TOOLS_VISION = Candidate(
    provider="openai",
    model=ModelInfo(
        id="gpt-4o",
        context_window=128_000,
        supports_tools=True,
        supports_json_mode=True,
        supports_vision=True,
        pricing=ModelPricing(input_per_1k_usd=0.005, output_per_1k_usd=0.015),
    ),
)
SMALL_TEXT_ONLY = Candidate(
    provider="ollama",
    model=ModelInfo(id="tiny", context_window=8_000, supports_tools=False),
)


def _catalog() -> Catalog:
    return Catalog([BIG_TOOLS_VISION, SMALL_TEXT_ONLY])


def test_no_requirements_returns_all() -> None:
    assert _catalog().capable(Requirements()) == [BIG_TOOLS_VISION, SMALL_TEXT_ONLY]


def test_min_context_excludes_small_models() -> None:
    capable = _catalog().capable(Requirements(min_context=32_000))
    assert capable == [BIG_TOOLS_VISION]


def test_tools_requirement_excludes_non_tool_models() -> None:
    assert _catalog().capable(Requirements(needs_tools=True)) == [BIG_TOOLS_VISION]


def test_vision_requirement_filters() -> None:
    assert _catalog().capable(Requirements(needs_vision=True)) == [BIG_TOOLS_VISION]


def test_unsatisfiable_requirements_return_nothing() -> None:
    # tiny model lacks tools; big model has only 128k — require an impossible context.
    assert _catalog().capable(Requirements(min_context=1_000_000)) == []
