"""Integration-test fixtures: real Postgres and Redis via testcontainers.

Containers are session-scoped (started once); the Postgres schema is created by
running the real Alembic migrations, so these tests exercise the migrations too.
The fixtures are synchronous and yield connection URLs — each async test builds
its own engine/client inside its own event loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from conduit.config import Settings
from conduit.infra.redis import create_redis_client

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _run_migrations(async_url: str) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", async_url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        _run_migrations(url)
        yield url


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as redis:
        host = redis.get_container_host_ip()
        port = redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(autouse=True)
async def _reset_redis(redis_url: str) -> AsyncIterator[None]:
    # Ephemeral Redis state (rate-limit buckets, breaker state) is keyed by fixed
    # provider/scope names and shared across the session-scoped container, so clear
    # it before each test for isolation. Reconstructible state — safe to flush.
    client = create_redis_client(Settings(redis_url=redis_url))
    try:
        await client.flushdb()
    finally:
        await client.aclose()
    yield
