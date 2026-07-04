"""Application factory and lifespan.

``create_app`` builds a fully wired FastAPI application from a :class:`Settings`
object. The lifespan constructs the shared clients (DB engine, Redis, httpx) on
startup and disposes them on shutdown — there are no module-level client
singletons, so every dependency is swappable in tests. A module-level ``app`` is
exposed for the uvicorn/gunicorn entrypoint (``conduit.main:app``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from conduit import __version__
from conduit.api import health
from conduit.api.errors import register_exception_handlers
from conduit.api.middleware import RequestContextMiddleware
from conduit.api.v1.admin import keys as admin_keys
from conduit.config import Settings
from conduit.infra.db.engine import create_db_engine
from conduit.infra.db.session import create_sessionmaker
from conduit.infra.redis import create_redis_client
from conduit.infra.telemetry.logging import configure_logging
from conduit.providers.registry import build_registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Construct shared clients on startup; dispose them on shutdown."""
    settings: Settings = app.state.settings

    engine = create_db_engine(settings)
    redis_client = create_redis_client(settings)
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(settings.request_timeout_seconds))

    app.state.db_engine = engine
    app.state.db_sessionmaker = create_sessionmaker(engine)
    app.state.redis = redis_client
    app.state.http_client = http_client
    app.state.provider_registry = build_registry(settings, http_client)

    try:
        yield
    finally:
        await http_client.aclose()
        await redis_client.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and wire the FastAPI application."""
    settings = settings or Settings()
    configure_logging(settings)

    app = FastAPI(
        title="Conduit",
        version=__version__,
        summary="OpenAI-compatible AI gateway.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(admin_keys.router)

    return app


app = create_app()
