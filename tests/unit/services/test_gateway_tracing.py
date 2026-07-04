"""Unit tests for gateway OpenTelemetry span trees (in-memory exporter)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from conduit.domain.routing.catalog import Catalog
from conduit.domain.routing.classes import ModelResolver
from conduit.domain.routing.engine import SmartRouter, StaticStrategy
from conduit.providers.registry import ProviderRegistry
from conduit.services.gateway import Gateway
from conduit.services.keys import Principal


class _RecordingUsage:
    async def record(self, **kwargs: Any) -> Decimal:
        return Decimal("0")


def _tracer() -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def _gateway(fake_provider_cls: Any, tracer: Tracer) -> Gateway:
    routes = {"fake-model": "fake"}
    router = SmartRouter(StaticStrategy(routes), ModelResolver(routes), Catalog([]))
    return Gateway(
        ProviderRegistry({"fake": fake_provider_cls(name="fake", model_ids=("fake-model",))}),
        router,
        _RecordingUsage(),  # type: ignore[arg-type]
        tracer=tracer,
    )


def _principal() -> Principal:
    return Principal(api_key_id=uuid.uuid4(), org_id=uuid.uuid4(), prefix="ck-testtest")


async def test_unary_emits_span_tree(fake_provider_cls, sample_request) -> None:
    tracer, exporter = _tracer()
    gateway = _gateway(fake_provider_cls, tracer)

    await gateway.chat_completion(sample_request, _principal())

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "gateway.chat_completion" in spans
    assert "gateway.route" in spans
    assert "provider.request" in spans
    root = spans["gateway.chat_completion"]
    assert root.attributes["conduit.provider"] == "fake"
    assert root.attributes["conduit.model"] == "fake-model"
    assert root.attributes["conduit.status"] == "ok"


async def test_streaming_emits_span_tree(fake_provider_cls, sample_request) -> None:
    tracer, exporter = _tracer()
    gateway = _gateway(fake_provider_cls, tracer)

    chunks = [chunk async for chunk in gateway.stream_chat_completion(sample_request, _principal())]
    assert chunks  # consumed the stream

    names = {span.name for span in exporter.get_finished_spans()}
    assert "gateway.stream_chat_completion" in names
    assert "gateway.route" in names
    assert "provider.request" in names
