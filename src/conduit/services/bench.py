"""Benchmarking harness: drive a workload across providers/models and summarize.

Runs a configurable number of requests per target through the provider adapters,
timing each call and computing cost from usage x catalog pricing, then reports
latency percentiles, throughput, cost, and error rate. Mocked by default (an
in-process ``_MockProvider`` registry); ``--live`` on the CLI drives real
backends through the same code path.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from decimal import Decimal

from conduit.domain.errors import ConduitError
from conduit.domain.optimize.bench import percentile
from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    Usage,
)
from conduit.providers.base import HealthStatus, ModelInfo
from conduit.providers.ollama import DEFAULT_OLLAMA_MODELS
from conduit.providers.openai import DEFAULT_OPENAI_MODELS
from conduit.providers.registry import ProviderRegistry
from conduit.services.usage import compute_cost, provider_pricing


@dataclass(frozen=True, slots=True)
class BenchSummary:
    """Aggregated results for one (provider, model) target."""

    provider: str
    model: str
    requests: int
    errors: int
    error_rate: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    total_cost_usd: Decimal
    throughput_rps: float


class Benchmark:
    """Runs a workload across targets and summarizes per target."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def run(
        self,
        targets: Sequence[tuple[str, str]],
        request: ChatCompletionRequest,
        *,
        per_target: int,
        concurrency: int,
    ) -> list[BenchSummary]:
        return [
            await self._run_target(provider, model, request, per_target, concurrency)
            for provider, model in targets
        ]

    async def _run_target(
        self,
        provider_name: str,
        model: str,
        request: ChatCompletionRequest,
        per_target: int,
        concurrency: int,
    ) -> BenchSummary:
        provider = self._registry.get(provider_name)
        pricing = provider_pricing(self._registry, provider_name, model)
        target_request = request.model_copy(update={"model": model})
        semaphore = asyncio.Semaphore(max(1, concurrency))
        latencies: list[float] = []
        cost = Decimal("0")
        errors = 0

        async def one() -> None:
            nonlocal cost, errors
            async with semaphore:
                start = time.perf_counter()
                try:
                    response = await provider.chat_completion(target_request)
                except ConduitError:
                    errors += 1
                    return
                latencies.append((time.perf_counter() - start) * 1000)
                if response.usage is not None:
                    cost += compute_cost(
                        pricing, response.usage.prompt_tokens, response.usage.completion_tokens
                    )

        wall_start = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(per_target)))
        wall = time.perf_counter() - wall_start
        return BenchSummary(
            provider=provider_name,
            model=model,
            requests=per_target,
            errors=errors,
            error_rate=errors / per_target if per_target else 0.0,
            p50_ms=percentile(latencies, 0.50),
            p95_ms=percentile(latencies, 0.95),
            p99_ms=percentile(latencies, 0.99),
            total_cost_usd=cost,
            throughput_rps=(per_target / wall) if wall > 0 else 0.0,
        )


class _MockProvider:
    """In-process provider for benchmarking without a live backend."""

    def __init__(self, name: str, models: Sequence[ModelInfo], *, delay_s: float = 0.002) -> None:
        self.name = name
        self._models = list(models)
        self._delay_s = delay_s

    @property
    def models(self) -> Sequence[ModelInfo]:
        return self._models

    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        await asyncio.sleep(self._delay_s)
        return ChatCompletionResponse(
            id="bench",
            created=0,
            model=request.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatCompletionMessage(role="assistant", content="ok"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        )

    def stream_chat_completion(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        raise NotImplementedError("benchmark mock does not stream")

    async def health(self) -> HealthStatus:
        return HealthStatus(healthy=True)


def build_mock_registry() -> ProviderRegistry:
    """A registry of in-process mock providers mirroring the built-ins' catalogs."""
    return ProviderRegistry(
        {
            "openai": _MockProvider("openai", DEFAULT_OPENAI_MODELS),
            "ollama": _MockProvider("ollama", DEFAULT_OLLAMA_MODELS),
        }
    )
