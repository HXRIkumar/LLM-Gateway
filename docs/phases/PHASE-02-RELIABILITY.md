# Phase 2 — Reliability: Task Checklist

Concrete, ordered tasks for the reliability layer. Work top to bottom. Tick each box when its acceptance note is satisfied, and keep `CLAUDE.md` §10 current. Do not start Phase 3 until every box here is checked and the Phase 2 **Definition of Done** in `docs/ROADMAP.md` holds.

This phase fills the seams left in `services/gateway.py`: **preflight** (rate limit + budget), **execute** (retry → breaker → fallback), and **account** (usage/cost). Reliability primitives are **pure policies in `domain/reliability/`**; their *state* lives in Redis/Postgres via `infra/` adapters (CLAUDE.md §4, ADR-0004). Nothing here may regress OpenAI compatibility — the Phase 1 compat gate must stay green.

Note on ordering: build order ≠ pipeline order. Accounting (pipeline stage 6) is built first because budgets (a preflight check) read from it.

Conventions: every task ships with tests and leaves `make check` green. Unit-test pure policies with injected clock/rng; integration-test anything touching Postgres/Redis on testcontainers with providers mocked via `respx`. New dependency this phase: **`arq`** (already pinned in CLAUDE.md §3 — no new ADR needed). Record the Redis fail-safe posture in **ADR-0005**.

---

## Task 1 — Usage & cost accounting (fill the `account` seam)
- [x] Alembic migration: `usage_record` (`org_id`, `api_key_id`, `provider`, `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd`, `latency_ms`, `status`, `created_at`), indexed on (`org_id`, `created_at`). See `docs/ARCHITECTURE.md` §9.
- [x] `services/usage.py`: compute `cost_usd` from the provider pricing metadata on `providers/base` (per-token pricing from Task 4 of Phase 1); persist one record per completed request.
- [x] Capture usage for **both** paths: unary from the response, streaming at stream end (after `[DONE]`), without breaking SSE framing.
- [x] Wire it into the gateway `account` stage (currently a no-op).
- **Accept:** integration test — a unary request writes an accurate row (tokens + computed cost + latency + status); a streaming request records usage on completion; `make check` green.

## Task 2 — Redis token-bucket rate limiting (preflight)
- [x] `domain/reliability/ratelimit.py`: pure limiter policy + `RateLimiter` port (limits in → allow/deny + retry-after out). No I/O.
- [x] `infra/redis.py`: atomic token-bucket via a **Lua script** (per-key and per-org windows) so concurrent requests can't over-admit.
- [x] Limits sourced from `Settings`/DB (requests-per-window per key/org; tokens-per-window optional).
- [x] Wire into gateway **preflight**; deny → OpenAI-shaped `429` with a `Retry-After` header.
- [x] Redis-down posture: **fail-open** (admit, emit a loud warning/metric) — record this in ADR-0005.
- **Accept:** integration test on real Redis (testcontainers) — bucket admits N then `429`s, refills over time, stays atomic under concurrent calls; `make check` green.

## Task 3 — Budgets (preflight)
- [x] Alembic migration: `budget` (org-scoped in Phase 2, `limit_usd`, `period` [daily/monthly], `status`). See `docs/ARCHITECTURE.md` §9. (Per-key scope + rolling window deferred; noted in ADR-0005.)
- [x] `services/budgets.py` (+ a pure check in `domain/reliability/budget.py`): compare accumulated spend (from Task 1's `usage_record`, the Postgres system of record) against the budget for the current period.
- [x] Wire into gateway **preflight**; over-budget → OpenAI-shaped billing error (`429`, type `insufficient_quota`). Fail-safe posture recorded in ADR-0005 (prefer conservative for spend).
- [x] Admin endpoints under `api/v1/admin/` to set/list/inspect budgets (guarded by the admin key).
- **Accept:** integration test — requests under budget pass, over budget are rejected with the correct envelope; the window resets per `period`; `make check` green.

## Task 4 — Retry policy (harden `execute`)
- [x] `domain/reliability/retry.py`: pure bounded-retry policy — max attempts, exponential backoff **+ jitter**, and retryable-vs-terminal classification (retry on timeouts / upstream `429` / `5xx`; **never** on client `4xx`, and **never** once response bytes have begun streaming — the streaming path isn't retried).
- [x] Wire around provider execution in the `execute` stage; inject clock/sleeper + rng for determinism.
- **Accept:** unit tests cover backoff/jitter schedule and the retryable/terminal split; integration test (`respx`) — a transient `503`-then-success is retried, a `400` is not; `make check` green.

## Task 5 — Per-provider circuit breaker (harden `execute`)
- [ ] `domain/reliability/breaker.py`: pure breaker state machine (closed → open → half-open) with failure threshold + cooldown.
- [ ] `infra/redis.py`: **shared** breaker state per provider (so every worker/process agrees), with atomic transitions.
- [ ] Wire into `execute`: when open, fast-fail the provider (handing off to fallback in Task 6) instead of calling it.
- **Accept:** unit tests for all state transitions; integration test (real Redis) — consecutive failures open the breaker, calls fast-fail while open, a half-open probe closes it on success; `make check` green.

## Task 6 — Automatic fallback (complete `execute`)
- [ ] `domain/reliability/fallback.py`: walk the `RoutingDecision` fallback plan — on a retryable failure or an open breaker, try the next provider/model in the plan; exhaustion → `AllProvidersFailed`.
- [ ] Integrate retry + breaker + fallback into one coherent `execute` path in `services/gateway.py` (order: try target under retry+breaker → on terminal failure walk the plan → else `AllProvidersFailed`).
- **Accept:** integration test (`respx` + real Redis) — primary failing or breaker-open lands on the fallback per the plan; all-fail maps to a clean OpenAI-shaped `5xx`; the same canonical schema flows unchanged across the fallback; `make check` green.

## Task 7 — Health probes & `arq` workers
- [ ] Introduce `arq` (Redis-native): worker settings + a `make worker` target.
- [ ] `workers/`: a periodic per-provider **health probe** that feeds breaker/health state, and a **usage rollup** job (e.g. per-org daily aggregates) supporting budgets/reporting.
- [ ] Surface per-provider health (via `/readyz` or an admin health endpoint).
- **Accept:** integration test — a worker probe cycle updates health/breaker state in Redis; a rollup job aggregates `usage_record` rows correctly; `make check` green.

## Task 8 — Pipeline integration, docs & exit
- [ ] Confirm the full pipeline order per `CLAUDE.md` §5: preflight (rate limit + budget) → route → execute (retry → breaker → fallback) → account (usage) — all seams now live, none pulled past their stage.
- [ ] Write **ADR-0005** (reliability model + Redis fail-safe posture for rate limits vs budgets). Update `docs/ARCHITECTURE.md` (reliability section / breaker state diagram) and `CLAUDE.md` §10 to point at Phase 3.
- [ ] One end-to-end integration test exercising limit + budget + retry + breaker + fallback + accounting together on a single request path.
- **Accept:** the entire Phase 2 exit checklist below and the ROADMAP Phase 2 **DoD** hold; `make check` green.

---

### Phase 2 exit checklist
- [ ] Rate limits enforced per key/org, atomic via Lua, `429` + `Retry-After`, fail-open on a Redis outage (per ADR-0005).
- [ ] Budgets enforced from real usage accounting; over-budget → correct OpenAI billing envelope; resets per period.
- [ ] Usage + cost persisted accurately for **both** unary and streaming.
- [ ] Retries bounded with backoff + jitter, on retryable errors only, never mid-stream.
- [ ] Per-provider circuit breakers trip and recover with shared Redis state.
- [ ] Fallback walks the routing plan; `AllProvidersFailed` handled cleanly.
- [ ] `arq` workers run health probes and usage rollups.
- [ ] `make check` green; integration tests use real Postgres + Redis (testcontainers) with providers mocked.
- [ ] OpenAI compatibility intact — the Phase 1 compat gate still passes (no regression).
- [ ] Docs (`ARCHITECTURE`, ADR-0005, `CLAUDE.md` §10) reflect reality.
