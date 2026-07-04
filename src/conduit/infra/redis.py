"""Async Redis client construction and a cheap readiness probe.

Redis holds only ephemeral, reconstructible fast-path state (ADR-0004); it is
never a source of truth. The client is created once in the app lifespan.
"""

from __future__ import annotations

import time

import structlog
from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url
from redis.exceptions import RedisError

from conduit.config import Settings
from conduit.domain.reliability.ratelimit import RateLimit, RateLimitResult

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


# Atomic token-bucket: refill, check, and consume in one round-trip so concurrent
# requests can't over-admit. Floats are returned as strings — Lua truncates numeric
# replies to integers.
_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then tokens = capacity; ts = now end
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill)
local allowed = 0
local retry_after = 0.0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
elseif refill > 0 then
  retry_after = (cost - tokens) / refill
else
  retry_after = -1
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], ttl_ms)
return {allowed, tostring(retry_after), tostring(tokens)}
"""


class RedisRateLimiter:
    """Atomic token-bucket ``RateLimiter`` backed by a Redis Lua script.

    Fails **open**: if Redis is unavailable the request is admitted (with a loud
    warning) rather than blocked — availability over strict limiting (ADR-0005).
    """

    def __init__(self, client: Redis) -> None:
        self._client = client
        self._script = client.register_script(_TOKEN_BUCKET_LUA)

    async def check(
        self,
        scope: str,
        limit: RateLimit,
        *,
        now: float | None = None,
        cost: float = 1.0,
    ) -> RateLimitResult:
        timestamp = time.time() if now is None else now
        ttl_ms = int(max(limit.window_seconds, 1.0) * 2 * 1000)
        try:
            raw = await self._script(
                keys=[f"ratelimit:{scope}"],
                args=[limit.capacity, limit.refill_per_second, timestamp, cost, ttl_ms],
            )
        except RedisError as exc:
            logger.warning("rate limiter unavailable; failing open", error=exc.__class__.__name__)
            return RateLimitResult(allowed=True, retry_after_seconds=0.0, remaining=0)
        return RateLimitResult(
            allowed=bool(int(raw[0])),
            retry_after_seconds=max(0.0, float(raw[1])),
            remaining=int(float(raw[2])),
        )
