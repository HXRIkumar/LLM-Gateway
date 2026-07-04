"""Redis-backed vector index for the semantic cache (brute-force cosine).

A bounded window of recent cacheable prompt vectors is kept in Redis; lookup
scans them and returns the nearest by cosine similarity. Brute force is fine for
a small recent window (ADR-0008) and keeps the dependency surface minimal — no
vector-DB. Fail-open: a Redis outage yields a miss / best-effort write, never an
error on the request path.
"""

from __future__ import annotations

import orjson
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from conduit.domain.optimize.semantic import cosine_similarity

logger = structlog.get_logger(__name__)

_KEYS = "semcache:keys"


class RedisVectorIndex:
    """Bounded brute-force vector index over recent cacheable prompts."""

    def __init__(self, client: Redis, *, max_entries: int, ttl_seconds: int) -> None:
        self._client = client
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _vec_key(key: str) -> str:
        return f"semcache:vec:{key}"

    async def add(self, key: str, vector: list[float]) -> None:
        try:
            pipe = self._client.pipeline()
            pipe.set(self._vec_key(key), orjson.dumps(vector), ex=self._ttl_seconds)
            pipe.lpush(_KEYS, key)
            pipe.ltrim(_KEYS, 0, self._max_entries - 1)
            await pipe.execute()
        except RedisError as exc:
            logger.warning("semantic index write failed", error=exc.__class__.__name__)

    async def nearest(self, vector: list[float]) -> tuple[str, float] | None:
        try:
            keys = await self._client.lrange(_KEYS, 0, self._max_entries - 1)  # type: ignore[misc]
            if not keys:
                return None
            raws = await self._client.mget([self._vec_key(k) for k in keys])
        except RedisError as exc:
            logger.warning("semantic index read failed", error=exc.__class__.__name__)
            return None
        best: tuple[str, float] | None = None
        for key, raw in zip(keys, raws, strict=True):
            if raw is None:
                continue
            similarity = cosine_similarity(vector, orjson.loads(raw))
            if best is None or similarity > best[1]:
                best = (key, similarity)
        return best
