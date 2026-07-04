# ADR-0005 — Reliability layer & Redis fail-safe posture

- **Status:** Accepted
- **Date:** 2026-07-05
- **Deciders:** Core maintainers

## Context
Phase 2 turns Conduit from a demo into infrastructure: rate limiting, budgets,
retries, circuit breakers, and automatic fallback. These primitives keep state in
two places (ADR-0004): **Redis** holds ephemeral fast-path state (rate-limit
buckets, breaker state, provider health) and **Postgres** holds durable state
(the usage ledger, budgets). We must define, up front, how each behaves when its
store is unavailable — otherwise the degraded path is discovered in production.

## Decision

**Reliability primitives are pure policies in `domain/reliability/`** (token
bucket, retry classification + backoff, breaker state machine, fallback walk),
unit-tested with injected clocks/RNG. Their *shared, atomic* state lives in Redis
via infra adapters using **Lua scripts** (single round-trip, so concurrent
requests cannot over-admit or race a transition). The gateway pipeline wires them
in the order fixed by CLAUDE.md §5:

```
preflight (rate limit → budget) → route → execute (breaker → retry → fallback) → account (usage)
```

**Fail-safe posture — the crux of this ADR:**

| Concern | Store | On store outage | Rationale |
|---|---|---|---|
| Rate limiting | Redis | **fail-open** (admit + warn) | A limiter that takes the gateway down is worse than briefly unlimited traffic. Availability > strict limiting. |
| Circuit breaker | Redis | **fail-open** (admit) | A breaker whose state store is down must not block all providers. |
| Provider health | Redis | treated as unknown | Advisory only; routing/execute still work. |
| **Budgets** | **Postgres** | **fail-closed by construction** | Spend caps are a correctness/billing guarantee, so they are enforced from the system of record, never Redis. If Postgres is down the request path fails — a durable-store outage is a distinct, louder failure class, not silent over-spend. |

So the two limit-like concerns differ deliberately: **rate limits relax** under a
Redis blip (they protect throughput, and a brief over-admit is cheap), while
**budgets stay strict** because they protect money and are backed by the durable
store.

Retries are bounded with exponential backoff + full jitter, only on transient
upstream errors (timeouts, upstream 429/5xx), never on client 4xx, and **never
once a response has begun streaming**. The breaker is per-provider
(closed → open → half-open). Fallback walks the routing decision's plan on a
retryable failure or an open breaker; a terminal (bad-request) failure is not
retried or fallen back — another provider won't fix a malformed request.

## Consequences
- Degradation is predictable and tested: a Redis outage loosens limits/breakers
  but keeps the gateway serving; budgets remain accurate because they never
  depended on Redis.
- Every reliability decision is unit-testable in isolation (pure policies) and
  the shared state is integration-tested against real Redis.
- The gateway never turns a provider hiccup into an unhandled 500: retry →
  breaker → fallback → a clean OpenAI-shaped error only when all options are
  exhausted (`AllProvidersFailed` → 502).

## Alternatives considered
- **Fail-closed rate limiting** — rejected: a Redis blip would become a full
  gateway outage, the opposite of a reliability feature.
- **Budgets in Redis** for speed — rejected: spend caps need durability and
  transactional correctness; serving from an ephemeral store risks silent
  over-spend on eviction/outage. Usage rollups (a worker job) provide the fast
  read path for reporting without moving the source of truth.
