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
from conduit.infra.db.engine import check_database
from conduit.infra.redis import check_redis
from conduit.providers.registry import ProviderRegistry


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


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a per-request session from the shared session factory."""
    factory = cast("async_sessionmaker[AsyncSession]", request.app.state.db_sessionmaker)
    async with factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbEngineDep = Annotated[AsyncEngine, Depends(get_db_engine)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]
ProviderRegistryDep = Annotated[ProviderRegistry, Depends(get_provider_registry)]


async def database_ready(engine: DbEngineDep) -> bool:
    return await check_database(engine)


async def redis_ready(client: RedisDep) -> bool:
    return await check_redis(client)


DatabaseReady = Annotated[bool, Depends(database_ready)]
RedisReady = Annotated[bool, Depends(redis_ready)]
