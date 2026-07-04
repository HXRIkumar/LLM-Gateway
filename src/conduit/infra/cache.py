"""Redis-backed response cache adapter (implements ``ResponseCache``).

Fail-open: a cache outage must never break the request path. ``get`` returns
``None`` (treated as a miss) and ``set`` is best-effort — both swallow Redis
errors with a warning rather than propagating (ADR-0004/0005 posture).
"""

from __future__ import annotations

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from conduit.domain.schemas import ChatCompletionResponse

logger = structlog.get_logger(__name__)


class RedisResponseCache:
    """Exact-match response cache in Redis with a per-entry TTL."""

    def __init__(self, client: Redis, ttl_seconds: int) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _redis_key(key: str) -> str:
        return f"cache:resp:{key}"

    async def get(self, key: str) -> ChatCompletionResponse | None:
        try:
            raw = await self._client.get(self._redis_key(key))
        except RedisError as exc:
            logger.warning("response cache read failed", error=exc.__class__.__name__)
            return None
        if raw is None:
            return None
        return ChatCompletionResponse.model_validate_json(raw)

    async def set(self, key: str, response: ChatCompletionResponse) -> None:
        try:
            await self._client.set(
                self._redis_key(key), response.model_dump_json(), ex=self._ttl_seconds
            )
        except RedisError as exc:
            logger.warning("response cache write failed", error=exc.__class__.__name__)
