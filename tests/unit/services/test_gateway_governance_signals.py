"""Phase 4 Task 4: breaker + governance internals surface as metrics and span events."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from conduit.domain.errors import BudgetExceeded, ConduitError, RateLimited
from conduit.domain.reliability.budget import BudgetDecision
from conduit.domain.reliability.ratelimit import RateLimit, RateLimitResult
from conduit.domain.routing.catalog import Catalog
from conduit.domain.routing.classes import ModelResolver
from conduit.domain.routing.engine import SmartRouter, StaticStrategy
from conduit.infra.telemetry.metrics import Metrics
from conduit.providers.registry import ProviderRegistry
from conduit.services.gateway import Gateway
from conduit.services.keys import Principal


def _tracer() -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


class _RecordingUsage:
    async def record(self, **kwargs: Any) -> Decimal:
        return Decimal("0")


class _RejectingRateLimiter:
    async def check(self, scope: str, limit: RateLimit) -> RateLimitResult:
        return RateLimitResult(allowed=False, retry_after_seconds=5.0, remaining=0)


class _RejectingBudget:
    async def check(self, org_id: uuid.UUID, **kwargs: Any) -> BudgetDecision:
        return BudgetDecision(
            allowed=False, spent=Decimal("10"), limit=Decimal("5"), remaining=Decimal("0")
        )


class _OpenBreaker:
    async def allow(self, provider: str) -> bool:
        return False

    async def record_success(self, provider: str) -> None: ...

    async def record_failure(self, provider: str) -> None: ...


def _router() -> SmartRouter:
    routes = {"fake-model": "fake"}
    return SmartRouter(StaticStrategy(routes), ModelResolver(routes), Catalog([]))


def _principal() -> Principal:
    return Principal(api_key_id=uuid.uuid4(), org_id=uuid.uuid4(), prefix="ck-testtest")


def _registry(fake_provider_cls: Any) -> ProviderRegistry:
    return ProviderRegistry({"fake": fake_provider_cls(name="fake", model_ids=("fake-model",))})


def _event_names(exporter: Any) -> set[str]:
    names: set[str] = set()
    for span in exporter.get_finished_spans():
        names.update(event.name for event in span.events)
    return names


async def test_ratelimit_rejection_in_metrics_and_traces(fake_provider_cls, sample_request) -> None:
    tracer, exporter = _tracer()
    metrics = Metrics()
    gateway = Gateway(
        _registry(fake_provider_cls),
        _router(),
        _RecordingUsage(),  # type: ignore[arg-type]
        rate_limiter=_RejectingRateLimiter(),  # type: ignore[arg-type]
        key_limit=RateLimit(requests=1, window_seconds=60),
        tracer=tracer,
        metrics=metrics,
    )

    with pytest.raises(RateLimited):
        await gateway.chat_completion(sample_request, _principal())

    assert metrics.registry.get_sample_value("conduit_ratelimit_rejections_total") == 1.0
    assert "ratelimit.rejected" in _event_names(exporter)


async def test_budget_rejection_in_metrics_and_traces(fake_provider_cls, sample_request) -> None:
    tracer, exporter = _tracer()
    metrics = Metrics()
    gateway = Gateway(
        _registry(fake_provider_cls),
        _router(),
        _RecordingUsage(),  # type: ignore[arg-type]
        budget=_RejectingBudget(),  # type: ignore[arg-type]
        tracer=tracer,
        metrics=metrics,
    )

    with pytest.raises(BudgetExceeded):
        await gateway.chat_completion(sample_request, _principal())

    assert metrics.registry.get_sample_value("conduit_budget_rejections_total") == 1.0
    assert "budget.rejected" in _event_names(exporter)


async def test_open_breaker_in_metrics_and_traces(fake_provider_cls, sample_request) -> None:
    tracer, exporter = _tracer()
    metrics = Metrics()
    gateway = Gateway(
        _registry(fake_provider_cls),
        _router(),
        _RecordingUsage(),  # type: ignore[arg-type]
        breaker=_OpenBreaker(),  # type: ignore[arg-type]
        tracer=tracer,
        metrics=metrics,
    )

    with pytest.raises(ConduitError):
        await gateway.chat_completion(sample_request, _principal())

    state = metrics.registry.get_sample_value("conduit_circuit_breaker_state", {"provider": "fake"})
    assert state == 1.0  # open
    assert "circuit_breaker.open" in _event_names(exporter)
