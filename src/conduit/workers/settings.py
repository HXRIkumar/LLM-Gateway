"""arq worker definition.

Run with ``arq conduit.workers.settings.WorkerSettings`` (see ``make worker``).
Startup builds the shared clients (engine, redis, httpx, registry, breaker,
health store) into the ctx; cron schedules the health probe (every minute) and
the usage rollup (hourly).
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from arq import cron
from arq.connections import RedisSettings

from conduit.config import Settings
from conduit.domain.reliability.breaker import BreakerConfig
from conduit.infra.db.engine import create_db_engine
from conduit.infra.db.session import create_sessionmaker
from conduit.infra.redis import ProviderHealthStore, RedisCircuitBreaker, create_redis_client
from conduit.providers.registry import build_registry
from conduit.workers.jobs import probe_providers, rollup_usage


async def startup(ctx: dict[str, Any]) -> None:
    settings = Settings()
    engine = create_db_engine(settings)
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(settings.request_timeout_seconds))
    redis_client = create_redis_client(settings)
    ctx["engine"] = engine
    ctx["http_client"] = http_client
    ctx["redis"] = redis_client
    ctx["sessionmaker"] = create_sessionmaker(engine)
    ctx["registry"] = build_registry(settings, http_client)
    ctx["breaker"] = RedisCircuitBreaker(
        redis_client,
        BreakerConfig(
            failure_threshold=settings.breaker_failure_threshold,
            cooldown_seconds=settings.breaker_cooldown_seconds,
        ),
    )
    ctx["health_store"] = ProviderHealthStore(redis_client)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http_client"].aclose()
    await ctx["redis"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [probe_providers, rollup_usage]
    cron_jobs: ClassVar[list[Any]] = [
        cron(probe_providers, second=0, run_at_startup=True),  # every minute
        cron(rollup_usage, minute=0),  # hourly
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(Settings().redis_url)
