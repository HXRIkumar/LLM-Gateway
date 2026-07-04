"""Async database engine construction and a cheap readiness probe.

The engine is created once in the app lifespan and shared. It is an infra
adapter: the only place (besides ``infra/db``) allowed to speak SQL.
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from conduit.config import Settings

logger = structlog.get_logger(__name__)


def create_db_engine(settings: Settings) -> AsyncEngine:
    """Build the shared async engine. Does not open a connection until first use."""
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


async def check_database(engine: AsyncEngine) -> bool:
    """Return True if a trivial round-trip to Postgres succeeds.

    A readiness probe: connection failures are reported as "not ready" rather
    than propagated. The error class (never the DSN, which carries credentials)
    is logged so operators can see why.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # readiness probe: report unhealthy, never propagate
        logger.warning("database readiness check failed", error=exc.__class__.__name__)
        return False
    return True
