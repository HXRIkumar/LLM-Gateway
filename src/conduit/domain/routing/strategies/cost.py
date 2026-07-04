"""Cost-optimized ranking (pure).

Rank capable candidates by estimated USD cost: prompt tokens + expected
completion tokens against per-token pricing. Cheapest first; ``sorted`` is stable
so ties break deterministically by the input (candidate) order.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from conduit.domain.routing.catalog import Candidate
from conduit.domain.routing.classify import estimate_prompt_tokens
from conduit.domain.schemas import ChatCompletionRequest
from conduit.providers.base import ModelPricing

DEFAULT_COMPLETION_TOKENS = 256


def estimate_cost(pricing: ModelPricing, prompt_tokens: int, completion_tokens: int) -> Decimal:
    return (Decimal(prompt_tokens) / 1000) * Decimal(str(pricing.input_per_1k_usd)) + (
        Decimal(completion_tokens) / 1000
    ) * Decimal(str(pricing.output_per_1k_usd))


def expected_completion_tokens(request: ChatCompletionRequest) -> int:
    return request.max_completion_tokens or request.max_tokens or DEFAULT_COMPLETION_TOKENS


def rank_by_cost(
    candidates: Sequence[Candidate], request: ChatCompletionRequest
) -> list[Candidate]:
    prompt = estimate_prompt_tokens(request)
    completion = expected_completion_tokens(request)
    return sorted(candidates, key=lambda c: estimate_cost(c.model.pricing, prompt, completion))
