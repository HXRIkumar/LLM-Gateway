"""Admin API for routing policies (guarded by the bootstrap admin key)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from conduit.api.deps import PolicyServiceDep
from conduit.api.middleware import require_admin
from conduit.infra.db.models import RoutingObjective

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class PolicyIn(BaseModel):
    objective: RoutingObjective = RoutingObjective.BALANCED
    allow_providers: list[str] | None = None
    deny_providers: list[str] | None = None
    org_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None


class PolicyOut(BaseModel):
    id: str
    scope: str
    objective: str
    allow_providers: list[str] | None
    deny_providers: list[str] | None
    status: str


def _out(row: object) -> PolicyOut:
    scope = f"key:{row.api_key_id}" if row.api_key_id else f"org:{row.org_id}"  # type: ignore[attr-defined]
    return PolicyOut(
        id=str(row.id),  # type: ignore[attr-defined]
        scope=scope,
        objective=row.objective,  # type: ignore[attr-defined]
        allow_providers=row.allow_providers,  # type: ignore[attr-defined]
        deny_providers=row.deny_providers,  # type: ignore[attr-defined]
        status=row.status,  # type: ignore[attr-defined]
    )


@router.post("/policies", status_code=201)
async def set_policy(payload: PolicyIn, policies: PolicyServiceDep) -> PolicyOut:
    row = await policies.upsert(
        objective=str(payload.objective),
        allow_providers=payload.allow_providers,
        deny_providers=payload.deny_providers,
        org_id=payload.org_id,
        api_key_id=payload.api_key_id,
    )
    return _out(row)


@router.get("/policies")
async def list_policies(policies: PolicyServiceDep) -> list[PolicyOut]:
    return [_out(row) for row in await policies.list_policies()]
