# ADR-0008 — Optimization: caching, dedup, semantic strategy, replay & adaptive routing

- **Status:** Accepted
- **Date:** 2026-07-05
- **Deciders:** Core maintainers

## Context
Phase 5 cuts cost and latency without changing client code. It wraps the
**execute** stage (CLAUDE.md §5 step 6) with cache read/write and feeds the
Phase 4 observability signals back into routing. Every optimization is optional
and off-by-default where it changes behaviour or stores data; a hit must be
indistinguishable from a fresh OpenAI-compatible response.

## Decision

**Exact-match cache.** A response is cacheable only when its output is
deterministic and single-shaped: `temperature == 0`, no tools/function calls, no
multimodal (vision) content, a single choice. The key is a sha256 over the
cacheability-relevant request fields (model, messages, and output-affecting
params) — `stream`, `user`, and `stream_options` are excluded, so a unary result
can replay as a stream. The store is Redis with a TTL and **fails open** (a cache
outage is a miss, never an error). `Cache-Control: no-store` bypasses. Read
happens after routing/preflight and before execute; write after a successful
execute; streaming reassembles then caches and replays cached hits as well-formed
SSE terminated by `[DONE]`.

**In-flight dedup (single-flight).** Concurrent identical cacheable requests
collapse into one upstream call; the rest await the shared result. Implemented as
an **in-process asyncio single-flight** (app-scoped, injected), *not* a Redis
poll-lock: it delivers exactly-one-call **and** clean failure-propagation to all
waiters (a failure is not memoized), which a polling lock handles poorly. This is
per-worker; cross-process concurrent duplicates are a documented non-goal —
sequential cross-process repeats are absorbed by the shared cache once the first
result lands.

**Semantic (near-match) cache.** Behind an `Embedder` port (OpenAI embeddings
adapter; swapped for a fake in tests) and a `SemanticIndex` port. The index is a
**Redis-backed brute-force cosine store over a bounded recent window** — no
vector database, keeping the dependency surface minimal; a real vector DB is a
future drop-in. On an exact-cache miss the request prompt is embedded, the nearest
indexed prompt is found, and its cached response is served when cosine similarity
≥ a configurable threshold. It reuses the exact response cache (the index maps a
vector to the same cache key) and shares the exact-cache safety guards, so
tool/vision/non-deterministic requests never semantic-hit. Off by default (needs
an embedding backend).

**Replay.** Capture is **opt-in and off by default**
(`CONDUIT_REPLAY_CAPTURE_ENABLED`). A `request_log` row stores the canonical
request + response — the bodies are the point of replay — but **never** the
Authorization header, gateway key plaintext, or upstream credentials (those never
reach the store). Replay (`POST /v1/admin/replays/{id}`, admin-guarded) re-runs a
captured request through the current pipeline with the cache bypassed, optionally
under a routing-policy override, so an operator can compare how a different
objective/allow-deny would route it.

**Cost prediction.** A coarse pre-flight estimate (character/token heuristic ×
catalog pricing) exposed additively via `POST /v1/estimate` (chosen route +
alternatives) and as a `conduit.estimated_cost_usd` span attribute. It is an
operator hint, **never** an input to billing (billing uses provider-reported
usage) and **never** alters the OpenAI-compatible chat contract.

**Adaptive routing.** The balanced strategy gains an error-rate term via an
`ErrorStats` port: a provider whose rolling error rate climbs is de-weighted and
returns as it recovers (latency was already fed in during Phase 3). A
`refresh_route_stats` arq worker maintains rolling per-(provider,model) error
rates in Redis from the usage ledger; `RedisRouteStats` reads them at route time
and **fails open** (no adaptation on a Redis outage). With no error data the
ranking is unchanged, so concrete-model routing and the compat gate are untouched.

**Benchmarking.** `conduit bench` drives a configurable workload across
providers/models and reports latency percentiles, throughput, cost, and error
rate. Mocked in-process providers by default; `--live` drives real backends
through the same code path.

## Consequences
- Repeat and near-repeat traffic is served without an upstream call, measurably
  cheaper and faster; concurrent duplicates collapse to one call.
- Routing degrades away from unhealthy providers automatically and recovers.
- New optional dependencies are contained: embeddings behind a port (no vector DB),
  no new heavy frameworks.
- The OpenAI-compatible contract is untouched — all additions are separate
  endpoints, opt-in behaviour, or internal signals; the compat gate still passes.

## Alternatives considered
- **Redis distributed lock for dedup** — rejected for the reasons above (failure
  propagation, complexity); revisit if cross-process concurrent dedup becomes a need.
- **A dedicated vector database for semantic cache** — rejected for now: a bounded
  Redis brute-force index is enough at current scale and adds no dependency.
- **Caching non-zero-temperature requests** — rejected: a cached hit must be a
  faithful stand-in, which only holds for deterministic requests.
- **Folding cost prediction into the chat response** — rejected: it would change
  the OpenAI-compatible contract; it lives in a separate endpoint + span attribute.
