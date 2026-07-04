"""Shared test fixtures.

``FakeProvider`` is a full in-memory implementation of the ``Provider`` protocol
used across the provider, routing, pipeline, and streaming tests — it proves the
abstraction holds without any network, and lets those tests drive success and
failure paths deterministically.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceDelta,
    ChunkChoice,
    Message,
    Usage,
)
from conduit.providers.base import HealthStatus, ModelInfo, ModelPricing


@pytest.fixture(autouse=True)
def _disable_otlp_export(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests must not attempt real OTLP export (a developer .env may set an
    # endpoint). Empty string → tracing configures to a clean no-op.
    monkeypatch.setenv("CONDUIT_OTEL_EXPORTER_OTLP_ENDPOINT", "")


class FakeProvider:
    """An in-memory ``Provider`` for tests (no network)."""

    def __init__(
        self,
        name: str = "fake",
        *,
        model_ids: Sequence[str] = ("fake-model",),
        response_text: str = "Hello from fake",
        healthy: bool = True,
        fail_with: Exception | None = None,
    ) -> None:
        self.name = name
        self._model_ids = list(model_ids)
        self._response_text = response_text
        self._healthy = healthy
        self._fail_with = fail_with
        self.received: list[ChatCompletionRequest] = []

    @property
    def models(self) -> Sequence[ModelInfo]:
        return [
            ModelInfo(
                id=model_id,
                context_window=8192,
                supports_tools=True,
                pricing=ModelPricing(input_per_1k_usd=0.001, output_per_1k_usd=0.002),
            )
            for model_id in self._model_ids
        ]

    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.received.append(request)
        if self._fail_with is not None:
            raise self._fail_with
        return ChatCompletionResponse(
            id="fake-cmpl-001",
            created=1700000000,
            model=request.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatCompletionMessage(role="assistant", content=self._response_text),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=7, completion_tokens=5, total_tokens=12),
        )

    async def stream_chat_completion(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        self.received.append(request)
        if self._fail_with is not None:
            raise self._fail_with

        def chunk(delta: ChoiceDelta, finish: str | None) -> ChatCompletionChunk:
            return ChatCompletionChunk(
                id="fake-cmpl-001",
                created=1700000000,
                model=request.model,
                choices=[ChunkChoice(index=0, delta=delta, finish_reason=finish)],
            )

        yield chunk(ChoiceDelta(role="assistant"), None)
        for word in self._response_text.split():
            yield chunk(ChoiceDelta(content=word + " "), None)
        yield chunk(ChoiceDelta(), "stop")

    async def health(self) -> HealthStatus:
        return HealthStatus(healthy=self._healthy, detail=None if self._healthy else "fake down")


@pytest.fixture
def fake_provider_cls() -> type[FakeProvider]:
    return FakeProvider


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def sample_request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="fake-model",
        messages=[Message(role="user", content="Hi there")],
    )
