"""Unit tests for the gateway pipeline (no HTTP, no DB)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.catalog import Catalog
from conduit.domain.routing.classes import ModelResolver
from conduit.domain.routing.engine import SmartRouter, StaticStrategy
from conduit.providers.registry import ProviderRegistry
from conduit.services.gateway import Gateway
from conduit.services.keys import Principal


class _RecordingUsage:
    """Stub UsageService: captures record() calls instead of hitting the DB."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


def _principal() -> Principal:
    return Principal(api_key_id=uuid.uuid4(), org_id=uuid.uuid4(), prefix="ck-testtest")


def _router(routes: dict[str, str]) -> SmartRouter:
    return SmartRouter(StaticStrategy(routes), ModelResolver(routes), Catalog([]))


def _gateway(fake_provider_cls: Any) -> tuple[Gateway, _RecordingUsage]:
    provider = fake_provider_cls(name="fake", model_ids=("fake-model",))
    usage = _RecordingUsage()
    gateway = Gateway(ProviderRegistry({"fake": provider}), _router({"fake-model": "fake"}), usage)  # type: ignore[arg-type]
    return gateway, usage


async def test_pipeline_routes_and_executes(fake_provider_cls, sample_request) -> None:
    gateway, usage = _gateway(fake_provider_cls)
    response = await gateway.chat_completion(sample_request, _principal())
    assert response.choices[0].message.content == "Hello from fake"
    # accounting fired once with the routed provider/model
    assert len(usage.records) == 1
    assert usage.records[0]["provider"] == "fake"
    assert usage.records[0]["model"] == "fake-model"


async def test_pipeline_unknown_model_raises_model_not_found(
    fake_provider_cls, sample_request
) -> None:
    provider = fake_provider_cls(name="fake", model_ids=("fake-model",))
    gateway = Gateway(ProviderRegistry({"fake": provider}), _router({}), _RecordingUsage())  # type: ignore[arg-type]
    with pytest.raises(ModelNotFound):
        await gateway.chat_completion(sample_request, _principal())


async def test_pipeline_streams_through_provider(fake_provider_cls, sample_request) -> None:
    gateway, usage = _gateway(fake_provider_cls)
    chunks = [chunk async for chunk in gateway.stream_chat_completion(sample_request, _principal())]
    assert chunks[-1].choices[0].finish_reason == "stop"
    text = "".join(c.choices[0].delta.content or "" for c in chunks).strip()
    assert text == "Hello from fake"
    # accounting fired once after the stream drained
    assert len(usage.records) == 1
