"""Admin replay endpoint: re-run a captured request through the current pipeline.

Loads a stored ``request_log`` record and replays it via the normal gateway
pipeline (fresh routing, reliability, accounting) — optionally under a policy
override so an operator can compare how a different objective/allow-deny would
route the same request. Cache is bypassed so the replay always re-executes.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from conduit.api.deps import GatewayDep, ReplayServiceDep
from conduit.api.middleware import require_admin
from conduit.domain.routing.policy import Policy
from conduit.domain.schemas import ChatCompletionRequest
from conduit.infra.db.models import RoutingObjective
from conduit.services.keys import Principal

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class ReplayPolicyOverride(BaseModel):
    """Optional routing-policy override for a replay (only affects alias routing)."""

    objective: RoutingObjective | None = None
    allow_providers: list[str] | None = None
    deny_providers: list[str] | None = None

    def to_policy(self) -> Policy | None:
        if self.objective is None and not self.allow_providers and not self.deny_providers:
            return None
        return Policy(
            objective=str(self.objective) if self.objective else "balanced",
            allow_providers=tuple(self.allow_providers) if self.allow_providers else None,
            deny_providers=tuple(self.deny_providers or ()),
        )


@router.post("/replays/{replay_id}")
async def replay_request(
    replay_id: uuid.UUID,
    gateway: GatewayDep,
    replays: ReplayServiceDep,
    override: ReplayPolicyOverride | None = None,
) -> Response:
    record = await replays.get(replay_id)
    if record is None:
        raise HTTPException(status_code=404, detail="replay record not found")
    request = ChatCompletionRequest.model_validate(record.request)
    principal = Principal(api_key_id=record.api_key_id, org_id=record.org_id, prefix="replay")
    policy_override = override.to_policy() if override is not None else None
    response = await gateway.chat_completion(
        request, principal, bypass_cache=True, policy_override=policy_override
    )
    return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
