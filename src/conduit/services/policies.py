"""Routing-policy persistence and resolution.

Resolves the *effective* policy for a request: a key-scoped policy overrides an
org-scoped one, and an unset scope falls back to the neutral default.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conduit.domain.routing.policy import DEFAULT_POLICY, Policy
from conduit.infra.db.models import RoutingPolicy


def _to_policy(row: RoutingPolicy) -> Policy:
    return Policy(
        objective=row.objective,
        allow_providers=tuple(row.allow_providers) if row.allow_providers else None,
        deny_providers=tuple(row.deny_providers or ()),
    )


class PolicyService:
    """Loads and stores routing policies."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def effective(self, *, org_id: uuid.UUID, api_key_id: uuid.UUID) -> Policy:
        async with self._sessionmaker() as session:
            key_policy = await session.scalar(
                select(RoutingPolicy).where(
                    RoutingPolicy.api_key_id == api_key_id, RoutingPolicy.status == "active"
                )
            )
            if key_policy is not None:
                return _to_policy(key_policy)
            org_policy = await session.scalar(
                select(RoutingPolicy).where(
                    RoutingPolicy.org_id == org_id,
                    RoutingPolicy.api_key_id.is_(None),
                    RoutingPolicy.status == "active",
                )
            )
            return _to_policy(org_policy) if org_policy is not None else DEFAULT_POLICY

    async def upsert(
        self,
        *,
        objective: str,
        allow_providers: list[str] | None = None,
        deny_providers: list[str] | None = None,
        org_id: uuid.UUID | None = None,
        api_key_id: uuid.UUID | None = None,
    ) -> RoutingPolicy:
        async with self._sessionmaker() as session:
            if api_key_id is not None:
                predicate = RoutingPolicy.api_key_id == api_key_id
            else:
                predicate = (RoutingPolicy.org_id == org_id) & RoutingPolicy.api_key_id.is_(None)
            row = await session.scalar(select(RoutingPolicy).where(predicate))
            if row is None:
                row = RoutingPolicy(org_id=org_id, api_key_id=api_key_id)
                session.add(row)
            row.objective = objective
            row.allow_providers = allow_providers
            row.deny_providers = deny_providers
            row.status = "active"
            await session.commit()
            await session.refresh(row)
            return row

    async def list_policies(self) -> list[RoutingPolicy]:
        async with self._sessionmaker() as session:
            return list(await session.scalars(select(RoutingPolicy)))
