"""Usage and cost accounting — the gateway's ``account`` stage.

Records one ledger row per completed request. Cost is computed from the chosen
provider's per-token pricing metadata (``providers/base.ModelPricing``). Records
are written through a fresh short-lived session (via the session *factory*, not
the request-scoped session) so accounting also works at the end of a stream,
after the request-scoped session would already be closed.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conduit.domain.errors import ProviderError
from conduit.domain.schemas import Usage
from conduit.infra.db.models import UsageRecord
from conduit.providers.base import ModelPricing
from conduit.providers.registry import ProviderRegistry

_PER_1K = Decimal(1000)


def compute_cost(pricing: ModelPricing, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """USD cost for a completion given per-1K pricing. Pure."""
    return (Decimal(prompt_tokens) / _PER_1K) * Decimal(str(pricing.input_per_1k_usd)) + (
        Decimal(completion_tokens) / _PER_1K
    ) * Decimal(str(pricing.output_per_1k_usd))


class UsageService:
    """Computes cost and persists usage records."""

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], registry: ProviderRegistry
    ) -> None:
        self._sessionmaker = sessionmaker
        self._registry = registry

    def _pricing(self, provider_name: str, model: str) -> ModelPricing:
        try:
            provider = self._registry.get(provider_name)
        except ProviderError:
            return ModelPricing()
        for advertised in provider.models:
            if advertised.id == model:
                return advertised.pricing
        return ModelPricing()

    async def record(
        self,
        *,
        org_id: uuid.UUID,
        api_key_id: uuid.UUID,
        provider: str,
        model: str,
        usage: Usage | None,
        latency_ms: int,
        status: str,
    ) -> Decimal:
        prompt = usage.prompt_tokens if usage else 0
        completion = usage.completion_tokens if usage else 0
        total = usage.total_tokens if usage else prompt + completion
        cost = compute_cost(self._pricing(provider, model), prompt, completion)
        async with self._sessionmaker() as session:
            session.add(
                UsageRecord(
                    org_id=org_id,
                    api_key_id=api_key_id,
                    provider=provider,
                    model=model,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    total_tokens=total,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    status=status,
                )
            )
            await session.commit()
        return cost
