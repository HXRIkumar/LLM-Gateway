# ADR-0007 — Observability: logs, traces, metrics & the cardinality/redaction contract

- **Status:** Accepted
- **Date:** 2026-07-05
- **Deciders:** Core maintainers

## Context
Phase 4 makes the gateway fully observable. The telemetry seams have existed
since Phase 1 (`infra/telemetry/`); this phase fills them and fixes the binding
rules for *where* instrumentation lives, *what* is emitted, and — most
importantly — *what must never* be emitted. Observability touches every request,
so the redaction and cardinality rules are as load-bearing as the OpenAI
compatibility contract.

## Decision

**Three pillars, one boundary.** Conduit emits structured logs (structlog),
distributed traces (OpenTelemetry → OTLP/HTTP), and metrics (`prometheus-client`,
scraped at `/metrics`). All three are wired in `infra/telemetry/` and instrumented
**only at the `services/` pipeline boundary** (`services/gateway.py`). `domain/`
stays pure — no tracer, no metrics, no logger threaded into routing/reliability
policies. This keeps the core unit-testable and framework-free (ADR-0003).

**No global singletons.** The `Tracer` and the `Metrics` registry are constructed
in the app lifespan and injected into the gateway. Tracing is a clean no-op when
`CONDUIT_OTEL_EXPORTER_OTLP_ENDPOINT` is unset; `/metrics` is gated by
`CONDUIT_METRICS_ENABLED`. Tests supply an in-memory span exporter and a private
`CollectorRegistry`, so telemetry assertions are deterministic and never leak
across tests or double-register on the global default registry.

**Tracing shape.** One root span per request — `gateway.chat_completion` /
`gateway.stream_chat_completion` — with a `gateway.route` child and a
per-attempt `provider.request` child. Governance/reliability events attach as
span events: `circuit_breaker.open`, `ratelimit.rejected`, `budget.rejected`.
Span attributes carry provider, model, objective, attempt, token counts, and
status — nothing else.

**Metrics shape.** RED plus domain series, all on the injected registry:
`conduit_requests_total{provider,model,status}`,
`conduit_request_duration_seconds` / `conduit_upstream_duration_seconds`
(histograms), `conduit_tokens_total{direction}`,
`conduit_cost_usd_total{provider,model}`,
`conduit_upstream_errors_total{provider,type}`, `conduit_retries_total{provider}`,
`conduit_ratelimit_rejections_total`, `conduit_budget_rejections_total`, and the
`conduit_circuit_breaker_state{provider}` gauge.

**Deployment.** Prometheus scrapes the api `/metrics` **directly** — metrics are
not shipped over OTLP. The OTel collector receives spans (OTLP gRPC 4317 / HTTP
4318) and exports them to its log (debug exporter) as the default pipeline; a
real backend (Tempo/Jaeger) is a drop-in exporter change. Grafana provisions the
Prometheus datasource and three dashboards (overview, per-provider, governance)
as code from `deploy/grafana/`. All of this lives behind the compose
`observability` profile (`make up-observability`).

**The cardinality & redaction contract (binding, extends CLAUDE.md §7):**
- Metric labels are restricted to low-cardinality dimensions — `provider`,
  `model`, `status`, `direction`, `type`. **Never** per-key, per-user, per-org,
  or per-request labels (that is unbounded cardinality and a cost/memory hazard).
- **Never** put secrets, API keys, upstream credentials, or full prompt/response
  bodies into any log field, span attribute, span event, metric label, or
  exemplar. A gateway key appears only as its non-secret hashed prefix.
- Every request emits exactly one correlated `request.completed` access log with
  a fixed field set; a test asserts both the presence of the required fields and
  the absence of the forbidden ones.

## Consequences
- Observability is opt-in per signal and zero-cost when disabled; production turns
  it on via config only.
- Instrumentation is centralized at one seam, so adding a signal or a label is a
  local change with a clear cardinality review point.
- The domain remains pure and fast to test; telemetry never dictates domain shape.

## Alternatives considered
- **Ship metrics over OTLP → collector → Prometheus remote-write** — rejected:
  an extra hop and moving part for no gain; `prometheus-client` scrape is the
  standard and keeps the api self-describing at `/metrics`.
- **Global OTel/Prometheus singletons** — rejected: untestable and a source of
  cross-test contamination; injected tracer + private registry are strictly
  better for a library-grade codebase.
- **Rich per-key/per-model-version metric labels** — rejected: unbounded
  cardinality. High-cardinality attribution belongs in the usage ledger
  (Postgres) and logs/traces, not in metric label sets.
