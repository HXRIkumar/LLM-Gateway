# ADR-0004 — Data stores: Postgres of record + Redis fast path

- **Status:** Accepted
- **Date:** 2026-01-05
- **Deciders:** Core maintainers

## Context
Conduit has two very different data needs. Some data is **durable and relational**: organizations, API keys, usage/cost records, budgets, routing policies, audit. Other data is **ephemeral and hot-path**: rate-limit token buckets, circuit-breaker counters/state, provider health, and (later) semantic-cache entries — read and written on nearly every request, where a millisecond matters and permanence does not.

## Decision
Use **PostgreSQL 16 as the system of record** (async SQLAlchemy 2.0 + asyncpg + Alembic migrations) and **Redis 7 as the fast-path state store** (`redis-py` async). Durable truth lives only in Postgres; Redis holds only reconstructible, expiring state. Rate limiting and breaker counters are updated atomically in Redis (Lua where needed). The system must **fail safe** if Redis is unavailable: limits and breakers fall back to conservative defaults and the request path stays up; no durable data is ever lost because it was only in Redis.

## Consequences
- Clean separation: transactional integrity and reporting come from Postgres; per-request throughput comes from Redis.
- Horizontal scale is straightforward — stateless API replicas share Redis and Postgres.
- Two datastores to operate and test; integration tests use **testcontainers** to run real instances of both.
- We must define, and test, the degraded behavior when Redis is down (fail-safe defaults) rather than discovering it in production.
- Redis keys carry TTLs and are namespaced (`ratelimit:*`, `breaker:*`, `health:*`, `cache:*`); nothing there is treated as authoritative.

## Alternatives considered
- **Postgres only** — one store to run, but per-request counters/limits/caching hammer the relational DB and add latency it isn't suited for. Rejected for the hot path.
- **Redis only** — fast, but unacceptable for durable, queryable records (usage, cost, audit) that need transactions and reporting. Rejected as system of record.
- **A dedicated time-series DB for usage now** — premature; usage rows in Postgres are sufficient through V4, with metrics handled by Prometheus. Deferred.
