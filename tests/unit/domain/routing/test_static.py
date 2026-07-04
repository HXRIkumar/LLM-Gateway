"""Unit tests for the static routing strategy and decision object."""

from __future__ import annotations

import pytest

from conduit.domain.errors import ModelNotFound
from conduit.domain.routing.engine import StaticStrategy
from conduit.domain.routing.strategy import RoutingDecision, RoutingStrategy
from conduit.domain.schemas import ChatCompletionRequest, Message


def _request(model: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=model, messages=[Message(role="user", content="hi")])


def test_static_strategy_satisfies_protocol() -> None:
    assert isinstance(StaticStrategy({}), RoutingStrategy)


def test_routes_mapped_model_to_its_provider() -> None:
    strategy = StaticStrategy({"gpt-4o-mini": "openai", "llama3.2": "ollama"})

    openai_decision = strategy.route(_request("gpt-4o-mini"))
    assert isinstance(openai_decision, RoutingDecision)
    assert openai_decision.provider == "openai"
    assert openai_decision.model == "gpt-4o-mini"
    assert "openai" in openai_decision.reason
    assert openai_decision.fallbacks == ()

    ollama_decision = strategy.route(_request("llama3.2"))
    assert ollama_decision.provider == "ollama"
    assert ollama_decision.model == "llama3.2"


def test_unmapped_model_raises_model_not_found() -> None:
    strategy = StaticStrategy({"gpt-4o-mini": "openai"})
    with pytest.raises(ModelNotFound) as exc_info:
        strategy.route(_request("does-not-exist"))
    assert exc_info.value.status_code == 404
    assert exc_info.value.param == "model"


def test_default_provider_is_used_as_catch_all() -> None:
    strategy = StaticStrategy({"gpt-4o-mini": "openai"}, default_provider="ollama")
    decision = strategy.route(_request("some-other-model"))
    assert decision.provider == "ollama"
    assert decision.model == "some-other-model"
