"""OpenAI-compatible chat completions endpoint (unary + streaming).

Delegates to the gateway pipeline. Non-streaming responses are serialized straight
from the canonical model (extras preserved, nulls dropped). Streaming responses
are Server-Sent Events: one ``data: <chunk>`` line per ``chat.completion.chunk``,
terminated by ``data: [DONE]``.

The stream is *primed* — the first chunk is pulled before the ``200`` is sent — so
routing or provider failures surface as proper OpenAI error envelopes instead of a
half-open 200 stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import orjson
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

from conduit.api.deps import GatewayDep
from conduit.api.middleware import CurrentPrincipal
from conduit.domain.schemas import ChatCompletionChunk, ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["chat"])


def _encode_event(chunk: ChatCompletionChunk) -> bytes:
    return b"data: " + orjson.dumps(chunk.model_dump(exclude_none=True)) + b"\n\n"


async def _sse_body(
    first: ChatCompletionChunk | None,
    rest: AsyncIterator[ChatCompletionChunk],
) -> AsyncIterator[bytes]:
    if first is not None:
        yield _encode_event(first)
        async for chunk in rest:
            yield _encode_event(chunk)
    yield b"data: [DONE]\n\n"


async def _streaming_response(stream: AsyncIterator[ChatCompletionChunk]) -> StreamingResponse:
    # Prime the stream: any routing/provider error raises here, before the 200.
    iterator = stream.__aiter__()
    try:
        first: ChatCompletionChunk | None = await iterator.__anext__()
    except StopAsyncIteration:
        first = None
    return StreamingResponse(_sse_body(first, iterator), media_type="text/event-stream")


@router.post("/chat/completions")
async def create_chat_completion(
    payload: ChatCompletionRequest,
    principal: CurrentPrincipal,
    gateway: GatewayDep,
) -> Response:
    if payload.stream:
        return await _streaming_response(gateway.stream_chat_completion(payload, principal))
    response = await gateway.chat_completion(payload, principal)
    return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
