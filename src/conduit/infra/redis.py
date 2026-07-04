"""Async Redis client construction and a cheap readiness probe.

Redis holds only ephemeral, reconstructible fast-path state (ADR-0004); it is
never a source of truth. The client is created once in the app lifespan.
"""

from __future__ import annotations

import structlog
from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url

from conduit.config import Settings

logger = structlog.get_logger(__name__)


def create_redis_client(settings: Settings) -> Redis:
    """Build the shared async Redis client. Connects lazily on first command."""
    return redis_from_url(settings.redis_url, encoding="utf-8", decode_responses=True)


async def check_redis(client: Redis) -> bool:
    """Return True if Redis answers PING; report (not raise) on failure."""
    try:
        pong = await client.ping()
    except Exception as exc:  # readiness probe: report unhealthy, never propagate
        logger.warning("redis readiness check failed", error=exc.__class__.__name__)
        return False
    return bool(pong)
