"""FastAPI dependencies.

Shared clients live on ``app.state`` (constructed in the lifespan, never as
module-level globals) and are exposed here as injectable dependencies so every
handler and every test can swap them. ``Annotated[...]`` aliases keep handler
signatures readable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast

import httpx
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from conduit.config import Settings
from conduit.domain.reliability.breaker import BreakerConfig
from conduit.domain.reliability.ratelimit import RateLimit
from conduit.domain.reliability.retry import RetryPolicy
from conduit.domain.routing.strategy import RoutingStrategy
from conduit.infra.db.engine import check_database
from conduit.infra.redis import (
    ProviderHealthStore,
    RedisCircuitBreaker,
    RedisRateLimiter,
    check_redis,
)
from conduit.providers.registry import ProviderRegistry
from conduit.services.budgets import BudgetService
from conduit.services.gateway import Gateway
from conduit.services.policies import PolicyService
from conduit.services.usage import UsageService


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_db_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.db_engine)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_http_client(request: Request) -> httpx.AsyncClient:
    return cast(httpx.AsyncClient, request.app.state.http_client)


def get_provider_registry(request: Request) -> ProviderRegistry:
    return cast(ProviderRegistry, request.app.state.provider_registry)


def get_routing_strategy(request: Request) -> RoutingStrategy:
    return cast(RoutingStrategy, request.app.state.routing_strategy)


def get_db_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """The session factory itself — for units of work that outlive the request
    (e.g. accounting at the end of a stream, after the request session closes)."""
    return cast("async_sessionmaker[AsyncSession]", request.app.state.db_sessionmaker)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a per-request session from the shared session factory."""
    async with get_db_sessionmaker(request)() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbEngineDep = Annotated[AsyncEngine, Depends(get_db_engine)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]
ProviderRegistryDep = Annotated[ProviderRegistry, Depends(get_provider_registry)]
RoutingStrategyDep = Annotated[RoutingStrategy, Depends(get_routing_strategy)]
SessionmakerDep = Annotated["async_sessionmaker[AsyncSession]", Depends(get_db_sessionmaker)]


def get_gateway(
    registry: ProviderRegistryDep,
    routing: RoutingStrategyDep,
    sessionmaker: SessionmakerDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> Gateway:
    usage = UsageService(sessionmaker, registry)
    rate_limiter = None
    key_limit = None
    org_limit = None
    if settings.rate_limit_enabled:
        rate_limiter = RedisRateLimiter(redis)
        key_limit = RateLimit(
            settings.rate_limit_per_key_requests, settings.rate_limit_per_key_window_seconds
        )
        org_limit = RateLimit(
            settings.rate_limit_per_org_requests, settings.rate_limit_per_org_window_seconds
        )
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay_seconds,
        max_delay=settings.retry_max_delay_seconds,
    )
    breaker = None
    if settings.breaker_enabled:
        breaker = RedisCircuitBreaker(
            redis,
            BreakerConfig(
                failure_threshold=settings.breaker_failure_threshold,
                cooldown_seconds=settings.breaker_cooldown_seconds,
            ),
        )
    return Gateway(
        registry,
        routing,
        usage,
        rate_limiter=rate_limiter,
        key_limit=key_limit,
        org_limit=org_limit,
        budget=BudgetService(sessionmaker),
        retry_policy=retry_policy,
        breaker=breaker,
        policy_service=PolicyService(sessionmaker),
    )


GatewayDep = Annotated[Gateway, Depends(get_gateway)]


def get_policy_service(sessionmaker: SessionmakerDep) -> PolicyService:
    return PolicyService(sessionmaker)


PolicyServiceDep = Annotated[PolicyService, Depends(get_policy_service)]


def get_budget_service(sessionmaker: SessionmakerDep) -> BudgetService:
    return BudgetService(sessionmaker)


BudgetServiceDep = Annotated[BudgetService, Depends(get_budget_service)]


def get_health_store(redis: RedisDep) -> ProviderHealthStore:
    return ProviderHealthStore(redis)


HealthStoreDep = Annotated[ProviderHealthStore, Depends(get_health_store)]


async def database_ready(engine: DbEngineDep) -> bool:
    return await check_database(engine)


async def redis_ready(client: RedisDep) -> bool:
    return await check_redis(client)


DatabaseReady = Annotated[bool, Depends(database_ready)]
RedisReady = Annotated[bool, Depends(redis_ready)]
