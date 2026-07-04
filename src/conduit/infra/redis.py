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
from conduit.domain.reliability.breaker import BreakerConfig
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


# Breaker transitions run atomically in Redis so every worker sees one state.
_BREAKER_ALLOW_LUA = """
local state = redis.call('HGET', KEYS[1], 'state')
if state == false or state == 'closed' or state == 'half_open' then
  if state == false then return 'closed' end
  return state
end
local opened_at = tonumber(redis.call('HGET', KEYS[1], 'opened_at')) or 0
if (tonumber(ARGV[1]) - opened_at) >= tonumber(ARGV[2]) then
  redis.call('HSET', KEYS[1], 'state', 'half_open')
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]))
  return 'half_open'
end
return 'open'
"""

_BREAKER_RECORD_LUA = """
local outcome = ARGV[1]
if outcome == 'success' then
  redis.call('HSET', KEYS[1], 'state', 'closed', 'failures', 0)
else
  local state = redis.call('HGET', KEYS[1], 'state')
  local failures = (tonumber(redis.call('HGET', KEYS[1], 'failures')) or 0) + 1
  if state == 'half_open' or failures >= tonumber(ARGV[3]) then
    redis.call('HSET', KEYS[1], 'state', 'open', 'failures', failures, 'opened_at', ARGV[2])
  else
    redis.call('HSET', KEYS[1], 'state', 'closed', 'failures', failures)
  end
end
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]))
return 'OK'
"""


class RedisCircuitBreaker:
    """Shared per-provider ``CircuitBreaker`` backed by atomic Redis Lua scripts.

    Fails **open** (admits) on a Redis outage: a broken breaker must not take the
    whole gateway down (ADR-0005).
    """

    def __init__(self, client: Redis, config: BreakerConfig) -> None:
        self._client = client
        self._config = config
        self._allow_script = client.register_script(_BREAKER_ALLOW_LUA)
        self._record_script = client.register_script(_BREAKER_RECORD_LUA)

    def _ttl_ms(self) -> int:
        return int(max(self._config.cooldown_seconds * 4, 60.0) * 1000)

    async def allow(self, provider: str, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        try:
            state = await self._allow_script(
                keys=[f"breaker:{provider}"],
                args=[moment, self._config.cooldown_seconds, self._ttl_ms()],
            )
        except RedisError as exc:
            logger.warning("breaker unavailable; failing open", error=exc.__class__.__name__)
            return True
        return str(state) != "open"

    async def record_success(self, provider: str, *, now: float | None = None) -> None:
        await self._record("success", provider, now)

    async def record_failure(self, provider: str, *, now: float | None = None) -> None:
        await self._record("failure", provider, now)

    async def _record(self, outcome: str, provider: str, now: float | None) -> None:
        moment = time.time() if now is None else now
        try:
            await self._record_script(
                keys=[f"breaker:{provider}"],
                args=[outcome, moment, self._config.failure_threshold, self._ttl_ms()],
            )
        except RedisError as exc:
            logger.warning("breaker record failed", error=exc.__class__.__name__)
