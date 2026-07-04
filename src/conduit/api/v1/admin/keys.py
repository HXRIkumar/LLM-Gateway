"""Admin API for managing gateway keys.

All routes are guarded by the bootstrap admin key. The plaintext key is returned
only from the create endpoint, only once; list responses expose the prefix and
status but never the hash or the plaintext.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from conduit.api.deps import DbSessionDep
from conduit.api.middleware import require_admin
from conduit.services.keys import KeyService

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class KeyCreatedResponse(BaseModel):
    """Returned once at issue time — carries the plaintext key."""

    id: str
    org_id: str
    prefix: str
    key: str


class KeySummary(BaseModel):
    """A non-sensitive view of a stored key."""

    id: str
    org_id: str
    prefix: str
    status: str
    created_at: datetime
    revoked_at: datetime | None = None


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_key(session: DbSessionDep) -> KeyCreatedResponse:
    service = KeyService(session)
    org = await service.get_or_create_default_org()
    issued = await service.issue(org.id)
    return KeyCreatedResponse(
        id=str(issued.id),
        org_id=str(issued.org_id),
        prefix=issued.prefix,
        key=issued.plaintext,
    )


@router.get("/keys")
async def list_keys(session: DbSessionDep) -> list[KeySummary]:
    keys = await KeyService(session).list_keys()
    return [
        KeySummary(
            id=str(key.id),
            org_id=str(key.org_id),
            prefix=key.prefix,
            status=str(key.status),
            created_at=key.created_at,
            revoked_at=key.revoked_at,
        )
        for key in keys
    ]


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: uuid.UUID, session: DbSessionDep) -> dict[str, str]:
    await KeyService(session).revoke(key_id)
    return {"id": str(key_id), "status": "revoked"}
