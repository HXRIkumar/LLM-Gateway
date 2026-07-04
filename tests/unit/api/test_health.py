"""Unit tests for health endpoints and the request-context middleware.

These run without containers: the readiness dependencies are overridden so no
real Postgres/Redis is touched. Integration coverage of the real probes lands
with the datastore foundation (Task 2).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conduit.api.deps import database_ready, redis_ready
from conduit.config import Settings
from conduit.main import create_app


@pytest_asyncio.fixture
async def client_factory() -> AsyncIterator:
    clients: list[AsyncClient] = []

    async def _make(db_ok: bool | None = None, redis_ok: bool | None = None) -> AsyncClient:
        app = create_app(Settings())
        if db_ok is not None:
            app.dependency_overrides[database_ready] = lambda: db_ok
        if redis_ok is not None:
            app.dependency_overrides[redis_ready] = lambda: redis_ok
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()


@pytest.mark.asyncio
async def test_healthz_is_always_ok(client_factory) -> None:
    client = await client_factory()
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_ok_when_all_deps_up(client_factory) -> None:
    client = await client_factory(db_ok=True, redis_ok=True)
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["dependencies"] == {"database": "ok", "redis": "ok"}


@pytest.mark.asyncio
async def test_readyz_503_when_database_down(client_factory) -> None:
    client = await client_factory(db_ok=False, redis_ok=True)
    resp = await client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["dependencies"]["database"] == "error"
    assert body["dependencies"]["redis"] == "ok"


@pytest.mark.asyncio
async def test_request_id_is_returned(client_factory) -> None:
    client = await client_factory()
    resp = await client.get("/healthz")
    assert resp.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_request_id_is_propagated_when_supplied(client_factory) -> None:
    client = await client_factory()
    resp = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers["x-request-id"] == "trace-abc-123"
