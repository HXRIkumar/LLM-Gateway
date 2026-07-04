"""Admin endpoint surfacing per-provider health (from the probe worker)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from conduit.api.deps import HealthStoreDep, ProviderRegistryDep
from conduit.api.middleware import require_admin

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/providers/health")
async def provider_health(
    registry: ProviderRegistryDep,
    health: HealthStoreDep,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for name in registry.names():
        recorded = await health.get_health(name)
        result.append(
            recorded
            or {"provider": name, "healthy": None, "detail": "not probed yet", "checked_at": None}
        )
    return result
