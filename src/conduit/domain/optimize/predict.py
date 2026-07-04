"""Cost prediction: a pre-flight token + cost estimate for a request.

Pure — a coarse token estimate (characters/token heuristic) times the catalog's
per-1K pricing. It is deliberately approximate: an operator-facing hint, never an
input to billing (billing uses the provider's reported usage). Exposed additively
via ``POST /v1/estimate`` and as a span attribute — it never touches the
OpenAI-compatible chat contract.
"""

from __future__ import annotations

from decimal import Decimal

from conduit.domain.optimize.semantic import prompt_text
from conduit.domain.schemas import ChatCompletionRequest
from conduit.providers.base import ModelPricing

CHARS_PER_TOKEN = 4
DEFAULT_COMPLETION_TOKENS = 300
_PER_1K = Decimal(1000)


def estimate_prompt_tokens(request: ChatCompletionRequest) -> int:
    """Coarse prompt-token estimate from the request's text length."""
    return max(1, len(prompt_text(request)) // CHARS_PER_TOKEN)


def estimate_completion_tokens(request: ChatCompletionRequest) -> int:
    """Use the request's output cap when set, else a conservative default."""
    return request.max_completion_tokens or request.max_tokens or DEFAULT_COMPLETION_TOKENS


def estimate_cost(pricing: ModelPricing, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """USD cost for the given token counts under per-1K pricing. Pure."""
    return (Decimal(prompt_tokens) / _PER_1K) * Decimal(str(pricing.input_per_1k_usd)) + (
        Decimal(completion_tokens) / _PER_1K
    ) * Decimal(str(pricing.output_per_1k_usd))
