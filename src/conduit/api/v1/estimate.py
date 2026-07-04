"""Additive cost-prediction endpoint (non-standard; never part of the chat contract).

``POST /v1/estimate`` routes a request (without executing it) and returns the
pre-flight cost estimate for the chosen provider/model plus the fallback
alternatives. This is a Conduit extension — it does not alter, and is separate
from, the OpenAI-compatible ``/v1/chat/completions`` contract.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from conduit.api.deps import ProviderRegistryDep, RouterDep
from conduit.api.middleware import CurrentPrincipal
from conduit.domain.optimize.predict import (
    estimate_completion_tokens,
    estimate_cost,
    estimate_prompt_tokens,
)
from conduit.domain.routing.classify import classify
from conduit.domain.routing.policy import DEFAULT_POLICY
from conduit.domain.schemas import ChatCompletionRequest
from conduit.providers.registry import ProviderRegistry
from conduit.services.usage import provider_pricing

router = APIRouter(prefix="/v1", tags=["estimate"])


class CostEstimate(BaseModel):
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: str


class EstimateResponse(BaseModel):
    chosen: CostEstimate
    alternatives: list[CostEstimate]


def _estimate(
    registry: ProviderRegistry, request: ChatCompletionRequest, provider: str, model: str
) -> CostEstimate:
    prompt_tokens = estimate_prompt_tokens(request)
    completion_tokens = estimate_completion_tokens(request)
    cost = estimate_cost(
        provider_pricing(registry, provider, model), prompt_tokens, completion_tokens
    )
    return CostEstimate(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=str(cost),
    )


@router.post("/estimate")
async def estimate_cost_endpoint(
    payload: ChatCompletionRequest,
    principal: CurrentPrincipal,
    router: RouterDep,
    registry: ProviderRegistryDep,
) -> EstimateResponse:
    decision = router.route(payload, classify(payload), DEFAULT_POLICY, {})
    return EstimateResponse(
        chosen=_estimate(registry, payload, decision.provider, decision.model),
        alternatives=[
            _estimate(registry, payload, t.provider, t.model) for t in decision.fallbacks
        ],
    )
