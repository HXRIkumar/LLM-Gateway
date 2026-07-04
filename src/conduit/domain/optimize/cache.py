"""Response-cache policy: cacheability, key derivation, stream reassembly.

Pure — no I/O, no framework. The cache *store* is an infra adapter that
implements :class:`ResponseCache`; this module decides *whether* a request may be
cached and *under what key*, and converts between the unary and streaming shapes.

Safety (ADR-0008): a cached hit must be indistinguishable from a fresh response,
so we only cache requests whose output is deterministic and single-shaped —
``temperature == 0``, no tools/function calls, no multimodal (vision) content,
and a single choice. Anything else is never cached and always hits the provider.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

import orjson

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


def _is_multimodal(messages: list[Message]) -> bool:
    """A list-shaped content part is multimodal (may carry images) — unsafe to cache."""
    return any(isinstance(m.content, list) for m in messages)


def is_cacheable(request: ChatCompletionRequest) -> bool:
    """True when the request's output is deterministic and single-shaped."""
    if request.tools or request.tool_choice:
        return False
    if request.n is not None and request.n > 1:
        return False
    if _is_multimodal(request.messages):
        return False
    return request.temperature == 0.0


# Fields whose values change the produced completion — everything else (stream,
# user, stream_options) is irrelevant to the response body and excluded from the key.
_KEY_FIELDS = (
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "stop",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "response_format",
)


def cache_key(request: ChatCompletionRequest) -> str:
    """Stable sha256 over the cacheability-relevant request fields."""
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": [m.model_dump(exclude_none=True) for m in request.messages],
    }
    for field in _KEY_FIELDS:
        payload[field] = getattr(request, field)
    blob = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(blob).hexdigest()


def response_to_chunks(response: ChatCompletionResponse) -> list[ChatCompletionChunk]:
    """Render a cached unary response as a well-formed SSE chunk sequence."""
    choice = response.choices[0]
    base = {"id": response.id, "created": response.created, "model": response.model}
    return [
        ChatCompletionChunk(
            **base,
            choices=[ChunkChoice(index=0, delta=ChoiceDelta(role="assistant"))],
        ),
        ChatCompletionChunk(
            **base,
            choices=[ChunkChoice(index=0, delta=ChoiceDelta(content=choice.message.content or ""))],
        ),
        ChatCompletionChunk(
            **base,
            choices=[
                ChunkChoice(
                    index=0, delta=ChoiceDelta(), finish_reason=choice.finish_reason or "stop"
                )
            ],
            usage=response.usage,
        ),
    ]


def assemble_response(
    *,
    id: str,
    created: int,
    model: str,
    content: str,
    finish_reason: str | None,
    usage: Usage | None,
) -> ChatCompletionResponse:
    """Reassemble a streamed completion into a cacheable unary response."""
    return ChatCompletionResponse(
        id=id,
        created=created,
        model=model,
        choices=[
            Choice(
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content or None),
                finish_reason=finish_reason or "stop",
            )
        ],
        usage=usage,
    )


class ResponseCache(Protocol):
    """Port for the exact-match response cache (infra provides the Redis adapter)."""

    async def get(self, key: str) -> ChatCompletionResponse | None: ...
    async def set(self, key: str, response: ChatCompletionResponse) -> None: ...
