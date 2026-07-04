"""OpenTelemetry tracing setup.

Tracing is enabled only when ``CONDUIT_OTEL_EXPORTER_OTLP_ENDPOINT`` is set; when
it is not, ``configure_tracing`` returns ``None`` and ``trace.get_tracer`` yields
a no-op tracer — spans become zero-cost. The tracer is injected into the pipeline
(services boundary) so tests can supply an in-memory exporter without global state.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

from conduit.config import Settings

TRACER_NAME = "conduit.gateway"


def configure_tracing(settings: Settings) -> TracerProvider | None:
    """Build + install a TracerProvider when an OTLP endpoint is configured."""
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return None
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


def get_tracer(provider: TracerProvider | None = None) -> Tracer:
    """Return a tracer from ``provider`` (or the global one — no-op if unset)."""
    if provider is not None:
        return provider.get_tracer(TRACER_NAME)
    return trace.get_tracer(TRACER_NAME)
