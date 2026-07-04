"""Liveness and readiness endpoints.

``/healthz`` is a pure liveness probe — it answers 200 as long as the process is
up. ``/readyz`` reflects real dependency health: it returns 200 only when both
Postgres and Redis are reachable, otherwise 503, with a per-dependency breakdown.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from conduit.api.deps import DatabaseReady, RedisReady

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is running and can serve requests."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    response: Response,
    database_ok: DatabaseReady,
    redis_ok: RedisReady,
) -> dict[str, object]:
    """Readiness: dependencies (Postgres, Redis) are reachable."""
    ready = database_ok and redis_ok
    response.status_code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not_ready",
        "dependencies": {
            "database": "ok" if database_ok else "error",
            "redis": "ok" if redis_ok else "error",
        },
    }
