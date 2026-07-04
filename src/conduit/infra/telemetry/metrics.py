"""Prometheus metrics.

A per-app :class:`Metrics` bundle over its own ``CollectorRegistry`` (so multiple
app instances in a test process don't double-register on the global default).
Labels are deliberately low-cardinality — provider, model, status, direction,
error type — never per-key or per-user (that would explode cardinality).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

_BREAKER_STATE_CODE = {"closed": 0, "open": 1, "half_open": 2}


class Metrics:
    """The gateway's metric instruments, bound to one registry."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests_total = Counter(
            "conduit_requests_total",
            "Chat completion requests.",
            ["provider", "model", "status"],
            registry=self.registry,
        )
        self.request_duration = Histogram(
            "conduit_request_duration_seconds",
            "End-to-end request duration.",
            ["provider", "model"],
            registry=self.registry,
        )
        self.upstream_duration = Histogram(
            "conduit_upstream_duration_seconds",
            "Provider execution duration.",
            ["provider"],
            registry=self.registry,
        )
        self.tokens_total = Counter(
            "conduit_tokens_total",
            "Tokens processed.",
            ["direction"],
            registry=self.registry,
        )
        self.cost_usd_total = Counter(
            "conduit_cost_usd_total",
            "Computed spend in USD.",
            ["provider", "model"],
            registry=self.registry,
        )
        self.upstream_errors_total = Counter(
            "conduit_upstream_errors_total",
            "Upstream/provider errors.",
            ["provider", "type"],
            registry=self.registry,
        )
        self.retries_total = Counter(
            "conduit_retries_total",
            "Provider call retries.",
            ["provider"],
            registry=self.registry,
        )
        self.ratelimit_rejections_total = Counter(
            "conduit_ratelimit_rejections_total",
            "Requests rejected by rate limiting.",
            registry=self.registry,
        )
        self.budget_rejections_total = Counter(
            "conduit_budget_rejections_total",
            "Requests rejected by budgets.",
            registry=self.registry,
        )
        self.circuit_breaker_state = Gauge(
            "conduit_circuit_breaker_state",
            "Breaker state per provider (0=closed, 1=open, 2=half_open).",
            ["provider"],
            registry=self.registry,
        )

    def set_breaker_state(self, provider: str, state: str) -> None:
        self.circuit_breaker_state.labels(provider=provider).set(_BREAKER_STATE_CODE.get(state, 0))

    def render(self) -> bytes:
        return generate_latest(self.registry)
