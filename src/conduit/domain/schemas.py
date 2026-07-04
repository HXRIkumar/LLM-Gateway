"""Canonical, OpenAI-compatible request/response schemas.

This is the single internal lingua franca (ADR-0003): the public wire format and
the shape every provider adapter translates to and from. It is pure — it imports
only Pydantic, never a framework or a vendor SDK.

The models validate the well-known OpenAI fields strictly (types, required
``model`` and non-empty ``messages``) while accepting unknown fields
(``extra="allow"``). That keeps a stock OpenAI SDK working unmodified today and
tolerant of new OpenAI parameters tomorrow — the gateway forwards what it does
not yet model rather than rejecting it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant", "tool", "developer", "function"]

# Content is either a plain string or an array of typed parts (multimodal).
MessageContent = str | list[dict[str, Any]] | None


class Message(BaseModel):
    """A single chat message in the canonical (OpenAI) shape."""

    model_config = ConfigDict(extra="allow")

    role: Role
    content: MessageContent = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    refusal: str | None = None


class ChatCompletionRequest(BaseModel):
    """Body of ``POST /v1/chat/completions``."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[Message] = Field(min_length=1)

    frequency_penalty: float | None = None
    logit_bias: dict[str, float] | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    n: int | None = None
    presence_penalty: float | None = None
    response_format: dict[str, Any] | None = None
    seed: int | None = None
    stop: str | list[str] | None = None
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    user: str | None = None


class Usage(BaseModel):
    """Token accounting for a completion."""

    model_config = ConfigDict(extra="allow")

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionMessage(BaseModel):
    """The assistant message returned inside a non-streaming choice."""

    model_config = ConfigDict(extra="allow")

    role: str = "assistant"
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    refusal: str | None = None


class Choice(BaseModel):
    """One completion choice in a non-streaming response."""

    model_config = ConfigDict(extra="allow")

    index: int
    message: ChatCompletionMessage
    finish_reason: str | None = None
    logprobs: dict[str, Any] | None = None


class ChatCompletionResponse(BaseModel):
    """Body of a non-streaming ``chat.completion`` response."""

    model_config = ConfigDict(extra="allow")

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage | None = None
    system_fingerprint: str | None = None


class ChoiceDelta(BaseModel):
    """The incremental delta carried by a streaming chunk choice."""

    model_config = ConfigDict(extra="allow")

    role: str | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    refusal: str | None = None


class ChunkChoice(BaseModel):
    """One choice inside a streaming ``chat.completion.chunk``."""

    model_config = ConfigDict(extra="allow")

    index: int
    delta: ChoiceDelta
    finish_reason: str | None = None
    logprobs: dict[str, Any] | None = None


class ChatCompletionChunk(BaseModel):
    """A single Server-Sent Event payload in a streamed completion."""

    model_config = ConfigDict(extra="allow")

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChunkChoice]
    usage: Usage | None = None
    system_fingerprint: str | None = None


class ErrorDetail(BaseModel):
    """The inner object of an OpenAI-shaped error envelope."""

    message: str
    type: str
    param: str | None = None
    code: str | None = None


class ErrorResponse(BaseModel):
    """OpenAI-shaped error envelope: ``{"error": {...}}``."""

    error: ErrorDetail
