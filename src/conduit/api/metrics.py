"""Prometheus scrape endpoint (gated by ``CONDUIT_METRICS_ENABLED``)."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST

from conduit.api.deps import MetricsDep, SettingsDep

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(settings: SettingsDep, metrics: MetricsDep) -> Response:
    if not settings.metrics_enabled:
        return Response(status_code=404)
    return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)
