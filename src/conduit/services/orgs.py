"""Organization helpers shared by the key and budget services."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conduit.infra.db.models import Organization

DEFAULT_ORG_NAME = "default"


async def get_or_create_org(session: AsyncSession, name: str = DEFAULT_ORG_NAME) -> Organization:
    """Fetch the org by name, creating (and flushing) it if absent."""
    org = await session.scalar(select(Organization).where(Organization.name == name))
    if org is None:
        org = Organization(name=name)
        session.add(org)
        await session.flush()
    return org
