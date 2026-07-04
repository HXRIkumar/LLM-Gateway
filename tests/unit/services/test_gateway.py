"""Unit tests for the gateway pipeline (no HTTP, no DB)."""

from __future__ import annotations

import uuid

import pytest

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.engine import StaticStrategy
from conduit.providers.registry import ProviderRegistry
from conduit.services.gateway import Gateway
from conduit.services.keys import Principal


def _principal() -> Principal:
    return Principal(api_key_id=uuid.uuid4(), org_id=uuid.uuid4(), prefix="ck-testtest")


async def test_pipeline_routes_and_executes(fake_provider_cls, sample_request) -> None:
    provider = fake_provider_cls(name="fake", model_ids=("fake-model",))
    gateway = Gateway(ProviderRegistry({"fake": provider}), StaticStrategy({"fake-model": "fake"}))

    response = await gateway.chat_completion(sample_request, _principal())

    assert response.choices[0].message.content == "Hello from fake"
    assert provider.received == [sample_request]


async def test_pipeline_unknown_model_raises_model_not_found(sample_request) -> None:
    gateway = Gateway(ProviderRegistry({}), StaticStrategy({}))
    with pytest.raises(ModelNotFound):
        await gateway.chat_completion(sample_request, _principal())


async def test_pipeline_streams_through_provider(fake_provider_cls, sample_request) -> None:
    provider = fake_provider_cls(name="fake", model_ids=("fake-model",))
    gateway = Gateway(ProviderRegistry({"fake": provider}), StaticStrategy({"fake-model": "fake"}))

    chunks = [chunk async for chunk in gateway.stream_chat_completion(sample_request, _principal())]

    assert chunks[-1].choices[0].finish_reason == "stop"
    text = "".join(c.choices[0].delta.content or "" for c in chunks).strip()
    assert text == "Hello from fake"
