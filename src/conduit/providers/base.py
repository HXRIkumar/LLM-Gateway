"""The provider contract (ADR-0003).

Every backend — OpenAI, Ollama, and anything added later — implements the same
async :class:`Provider` protocol and advertises its models with capability and
pricing metadata. The rest of the system speaks only the canonical schema and
this protocol, so adding a provider is a local change: implement the protocol
and add one registry entry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)


class ModelPricing(BaseModel):
    """Per-token pricing, USD per 1K tokens. Feeds cost-aware routing (V3)."""

    input_per_1k_usd: float = 0.0
    output_per_1k_usd: float = 0.0


class ModelInfo(BaseModel):
    """Capability + pricing metadata for one advertised model."""

    id: str
    context_window: int
    supports_tools: bool = False
    supports_json_mode: bool = False
    supports_vision: bool = False
    pricing: ModelPricing = Field(default_factory=ModelPricing)


class HealthStatus(BaseModel):
    """Result of a provider health probe."""

    healthy: bool
    detail: str | None = None


@runtime_checkable
class Provider(Protocol):
    """Async adapter contract implemented by every provider.

    ``stream_chat_completion`` is declared as a plain method returning an async
    iterator (implementations are ``async def`` generators): calling it returns
    the iterator directly, no ``await``.
    """

    name: str

    @property
    def models(self) -> Sequence[ModelInfo]: ...

    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse: ...

    def stream_chat_completion(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]: ...

    async def health(self) -> HealthStatus: ...
