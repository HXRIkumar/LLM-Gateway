# Phase 4 — Observability: Task Checklist

Concrete, ordered tasks for tracing, metrics, and dashboards. Work top to bottom. Tick each box when its acceptance note is satisfied, and keep `CLAUDE.md` §10 current. Do not start Phase 5 until every box here is checked and the Phase 4 **Definition of Done** in `docs/ROADMAP.md` holds.

This phase fills the **account + observe** seam (`CLAUDE.md` §5 step 6) and the telemetry seams present since Phase 1 (`infra/telemetry/`). It makes the gateway fully observable: structured access logs, OpenTelemetry traces, Prometheus metrics, and provisioned Grafana dashboards — using the stack already pinned in `CLAUDE.md` §3 and the compose observability profile already scaffolded in Phase 1.

Non-negotiable privacy rule (extends `CLAUDE.md` §7): **never** put secrets, API keys, or full prompt/response bodies into logs, spans, metric labels, or exemplars. Log an API key only by its hashed prefix. Keep metric label cardinality bounded — labels are limited to low-cardinality dimensions (provider, model, status, route objective); never per-key/per-user labels.

Conventions: telemetry wiring lives in `infra/telemetry/` and is toggled by config (`CONDUIT_OTEL_*`, `CONDUIT_METRICS_ENABLED`) — disabled cleanly when unset. Instrument at the pipeline boundaries in `services/`, not inside `domain/`. Tests use an in-memory span exporter and a metrics scrape; compose/observability configs are validated by bringing the profile up. Record the observability approach in **ADR-0007**.

---

## Task 1 — Structured access logs, complete & correlated
- [x] Every request emits one structured `request.completed` access log: `request_id` (contextvar), key hashed prefix, chosen provider + model, route reason, attempts/providers-tried, breaker outcome, latency, token counts, cost, and final status. Consistent field names.
- [x] Audited: no secret, raw key, or prompt/response body appears in any log (test asserts absence).
- **Accept:** integration test asserts an access-log line contains the required fields and none of the forbidden ones; `make check` green.

## Task 2 — OpenTelemetry tracing (fill the trace seam)
- [x] `infra/telemetry/tracing.py`: initializes the OTel SDK + OTLP HTTP exporter (endpoint from `CONDUIT_OTEL_*`); wired into the lifespan; a clean no-op when unconfigured (injected tracer, no global state in tests).
- [x] Instrumented the pipeline: a root span per request (`gateway.chat_completion` / `gateway.stream_chat_completion`) with `gateway.route` and per-attempt `provider.request` child spans. Attributes: provider, model, objective, attempt, token counts, status — no secrets, no bodies.
- **Accept:** integration test with an in-memory span exporter asserts the expected span tree and attributes for a unary and a streaming request; `make check` green.

## Task 3 — Prometheus metrics (fill the metrics seam)
- [x] `infra/telemetry/metrics.py`: a prometheus-client registry and the core instruments — `requests_total{provider,model,status}`, `request_duration_seconds` (histogram), `upstream_duration_seconds`, `tokens_total{direction}`, `cost_usd_total{provider,model}`, `upstream_errors_total{provider,type}`, `retries_total{provider}`, `ratelimit_rejections_total`, `budget_rejections_total`, and `circuit_breaker_state{provider}`.
- [x] `GET /metrics` endpoint, gated by `CONDUIT_METRICS_ENABLED`. Keep label sets low-cardinality.
- **Accept:** integration test scrapes `/metrics`, asserts the key series exist and increment/observe correctly across a request (success + a forced upstream error), and asserts no unbounded labels; `make check` green.

## Task 4 — Provider & governance signals
- [ ] Surface Phase 2/3 internals as telemetry: per-provider health, circuit-breaker state transitions, observed latency, and rate-limit/budget rejections — as both metrics (Task 3 series) and span events/attributes where relevant.
- **Accept:** integration test — opening a breaker and hitting a rate limit/budget are reflected in the corresponding metric series and traces; `make check` green.

## Task 5 — OTel Collector + Prometheus + Grafana wiring
- [ ] Add the configs the Phase 1 compose observability profile references: `deploy/otel/` (collector pipeline: receive OTLP → export to Prometheus/logging), `deploy/prometheus/` (scrape config targeting the api `/metrics`), `deploy/grafana/` (datasource provisioning for Prometheus). Confirm `make up-observability` (or `docker compose --profile observability up`) is wired.
- **Accept:** the observability profile brings up api + postgres + redis + otel-collector + prometheus + grafana healthy; Prometheus reports the api target as `up` and the OTel collector receiving spans; `make check` green (all configs parse/validate).

## Task 6 — Grafana dashboards (the "dashboards" deliverable)
- [ ] Provision dashboards as JSON under `deploy/grafana/dashboards/` (auto-loaded via Grafana provisioning): a **Gateway Overview** (RPS, latency p50/p95/p99, error rate, tokens, spend), a **Per-Provider** view (health, breaker state, latency, error mix, share of traffic), and a **Governance** view (rate-limit and budget rejections, spend vs budget).
- [ ] Every panel references a metric series that Task 3/4 actually produces.
- **Accept:** dashboards load in the running Grafana without manual import (provisioning verified) and every panel query resolves against the live series; `make check` green.

## Task 7 — Docs & exit
- [ ] Write **ADR-0007** (observability approach: OTel + Prometheus + Grafana; cardinality and redaction rules; what is never labeled or logged). Update `docs/ARCHITECTURE.md` (observability section) and add a short "Observability" section to `README.md` (how to run the profile and open Grafana). Update `CLAUDE.md` §10 to point at Phase 5.
- **Accept:** the Phase 4 exit checklist below and the ROADMAP Phase 4 **DoD** hold; the OpenAI compatibility gate still passes; `make check` green.

---

### Phase 4 exit checklist
- [ ] Every request produces a complete, correlated structured access log with zero forbidden fields.
- [ ] OTel traces show a per-request span tree (stage spans + a provider-call span) with safe attributes; disabled cleanly when unconfigured.
- [ ] Prometheus metrics cover latency, throughput, errors, tokens, cost, retries, breaker state, and governance rejections, with bounded label cardinality; `/metrics` gated by config.
- [ ] `make up-observability` yields a healthy stack; Prometheus scrapes the api; the collector receives spans; Grafana auto-provisions the datasource and dashboards.
- [ ] Grafana dashboards (overview, per-provider, governance) load automatically and their panels resolve.
- [ ] `make check` green; integration tests use an in-memory span exporter and a metrics scrape; providers mocked, real Postgres + Redis via testcontainers.
- [ ] OpenAI compatibility intact — the compat gate still passes (no regression).
- [ ] Docs (`ARCHITECTURE`, ADR-0007, `README`, `CLAUDE.md` §10) reflect reality.
