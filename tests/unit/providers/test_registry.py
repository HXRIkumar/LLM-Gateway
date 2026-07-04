"""Unit tests for the provider protocol and registry.

Proves the abstraction: a fake in-memory provider satisfies the protocol and is
exercised end-to-end, and wiring it into the system took only an implementation
plus one registry entry.
"""

from __future__ import annotations

import pytest

from conduit.config import Settings
from conduit.domain.errors import ProviderError
from conduit.providers.base import Provider
from conduit.providers.registry import ProviderRegistry, build_registry


def test_fake_provider_satisfies_protocol(fake_provider: Provider) -> None:
    assert isinstance(fake_provider, Provider)


async def test_fake_provider_returns_canonical_response(fake_provider, sample_request) -> None:
    resp = await fake_provider.chat_completion(sample_request)
    assert resp.object == "chat.completion"
    assert resp.model == "fake-model"
    assert resp.choices[0].message.content == "Hello from fake"
    assert fake_provider.received == [sample_request]


def test_build_registry_from_single_factory(fake_provider_cls) -> None:
    factories = {"fake": lambda settings, http: fake_provider_cls(name="fake")}
    registry = build_registry(Settings(), http_client=None, factories=factories)
    assert registry.names() == ["fake"]
    assert "fake" in registry
    assert registry.get("fake").name == "fake"


def test_registry_aggregates_models_for_models_endpoint(fake_provider_cls) -> None:
    factories = {
        "a": lambda s, h: fake_provider_cls(name="a", model_ids=("m1", "m2")),
        "b": lambda s, h: fake_provider_cls(name="b", model_ids=("m3",)),
    }
    registry = build_registry(Settings(), http_client=None, factories=factories)
    assert {m.id for m in registry.all_models()} == {"m1", "m2", "m3"}


def test_registry_get_unknown_provider_raises() -> None:
    registry = ProviderRegistry({})
    with pytest.raises(ProviderError):
        registry.get("missing")


async def test_registry_provider_streams_end_to_end(fake_provider_cls, sample_request) -> None:
    registry = build_registry(
        Settings(), http_client=None, factories={"fake": lambda s, h: fake_provider_cls()}
    )
    provider = registry.get("fake")
    chunks = [chunk async for chunk in provider.stream_chat_completion(sample_request)]
    assert chunks[0].choices[0].delta.role == "assistant"
    assert chunks[-1].choices[0].finish_reason == "stop"
    text = "".join(c.choices[0].delta.content or "" for c in chunks).strip()
    assert text == "Hello from fake"
