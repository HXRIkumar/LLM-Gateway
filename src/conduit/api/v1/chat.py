"""OpenAI-compatible chat completions endpoint.

Delegates to the gateway pipeline. The response is serialized straight from the
canonical model (extras preserved, nulls dropped) so the body matches the OpenAI
wire format a stock SDK expects. Streaming (``stream: true``) lands in Task 10.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.responses import Response

from conduit.api.deps import GatewayDep
from conduit.api.middleware import CurrentPrincipal
from conduit.domain.schemas import ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions")
async def create_chat_completion(
    payload: ChatCompletionRequest,
    principal: CurrentPrincipal,
    gateway: GatewayDep,
) -> Response:
    response = await gateway.chat_completion(payload, principal)
    return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
