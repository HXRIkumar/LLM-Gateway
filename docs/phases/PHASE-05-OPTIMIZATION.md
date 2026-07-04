# Phase 5 — Optimization: Task Checklist

Concrete, ordered tasks for caching, deduplication, replay, cost prediction, adaptive routing, and benchmarking. Work top to bottom. Tick each box when its acceptance note is satisfied, and keep `CLAUDE.md` §10 current. This is the final planned phase; when its **Definition of Done** in `docs/ROADMAP.md` holds, the roadmap is complete.

This phase wraps the **execute** stage with a cache read/write (`CLAUDE.md` §5 step 6) and feeds the observability signals from Phase 4 back into routing. It builds on every prior layer: the canonical schema (P1), usage/cost + reliability state (P2), the routing engine and stats port (P3), and metrics/traces (P4).

Safety rules for this phase: a cache or dedup hit must be **indistinguishable** from a fresh OpenAI-compatible response (same shape, same streaming framing). Never serve a cached/semantic hit when it would be unsafe or wrong — e.g. tool/function calls, vision requests, or non-deterministic requests unless explicitly marked cacheable. Replay capture is **opt-in and off by default** and must honor the same secret/PII redaction rules as logging (`CLAUDE.md` §7).

Conventions: caching/dedup/adaptive policies are **pure** in `domain/` behind ports; their state (cache entries, vectors, locks, rolling stats) lives in Redis/Postgres via `infra/` adapters. Embeddings are reached through a mockable port so tests never need a live embedding backend. Integration tests use real Redis/Postgres (testcontainers) and respx-mocked providers; a respx assertion proves that a cache/dedup hit makes **no** upstream call. Record caching, dedup, semantic strategy, and replay privacy in **ADR-0008**. Justify new dependencies (embeddings/vector search) in the ADR.

---

## Task 1 — Exact-match response cache (foundation)
- [x] `domain/optimize/cache.py` (pure policy: cacheability + key derivation) + an `infra/` Redis-backed cache store with TTL. Key = a stable hash of the cacheability-relevant request fields (model, messages, and output-affecting params). A request is cacheable only when safe (e.g. `temperature == 0` or an explicit cacheable flag) — the exact rule is recorded in ADR-0008. Support an explicit bypass (header/param).
- [x] Wire a cache **read** before execute and a cache **write** after a successful execute, in the §5 step 6 seam — for both unary and streaming (reassemble then cache; replay as a well-formed SSE stream on hit).
- **Accept:** integration test (real Redis + respx) — an identical cacheable request is served from cache with **no** upstream call; a non-cacheable or bypassed request always hits the provider; a cached streaming response replays with correct SSE framing and `[DONE]`; `make check` green.

## Task 2 — In-flight deduplication (single-flight)
- [x] Collapse concurrent identical cacheable requests into one upstream call; the rest await the shared result. Pure coordination policy in `domain/`, Redis lock/single-flight in `infra/`. *Implemented as an in-process asyncio single-flight (app-scoped, injected) rather than a Redis poll-lock — it gives exactly-one-call **and** clean failure-propagation-to-all-waiters, which a polling lock handles poorly. Cross-process concurrency is a documented non-goal for now (sequential cross-process repeats are absorbed by the shared cache); rationale in ADR-0008.*
- **Accept:** integration test — N concurrent identical cacheable requests trigger exactly **one** upstream call (respx call-count assertion) and all callers receive the correct response; a failure is propagated to all waiters, not cached; `make check` green.

## Task 3 — Semantic cache (near-match) — ADR-0008
- [ ] An `Embedder` port (pure interface) + an `infra/` adapter (a provider/model behind config); a vector index over recent cacheable prompts (start with a Redis-backed vector store or a simple in-process index — choice recorded in ADR-0008).
- [ ] On a miss against the exact cache, embed the request and look up the nearest neighbor; serve the cached response when cosine similarity ≥ a configurable threshold. Miss → normal path. Apply the same safety guards as Task 1 (never for tool/vision/non-deterministic).
- **Accept:** integration test with a **mocked** embedder — a paraphrase within threshold produces a semantic hit (no upstream call); below threshold misses; guarded request types never semantic-hit; `make check` green.

## Task 4 — Replay capture (opt-in)
- [ ] Alembic migration: a `request_log` table (or an extension of `usage_record`) storing the canonical request + response in a replayable form, **off by default**, enabled per-key/org or globally by config, with the same redaction rules as logging.
- **Accept:** integration test — when enabled, a request is captured in replayable form; when disabled (default), nothing sensitive is stored; `make check` green.

## Task 5 — Replay mechanism
- [ ] A replay capability (CLI `conduit replay <id>` and/or an admin endpoint) that re-runs a captured request through the current gateway — optionally under a different policy — and returns a fresh response for comparison. Replays go through the normal pipeline.
- **Accept:** integration test — a captured request replays and returns a well-formed response; replaying under a different routing policy selects a different provider; `make check` green.

## Task 6 — Cost prediction
- [ ] `domain/optimize/predict.py`: estimate the cost of a request under the chosen route (and viable alternatives) from a token estimate × catalog pricing. Expose it — a response header and/or an additive `POST /v1/estimate` endpoint (clearly non-standard, never altering the OpenAI-compatible chat contract) — and emit it as a metric/span attribute.
- **Accept:** unit + integration tests — predicted cost is within a stated tolerance of the recorded actual for a known mocked completion; the estimate endpoint/header returns sane values; the standard chat contract is unchanged; `make check` green.

## Task 7 — Adaptive routing
- [ ] Feed rolling cost/latency/error stats (P2 usage + P3 stats port + P4 metrics) back into routing so the balanced strategy adapts over time (e.g. de-weight a provider whose error rate or latency is climbing). Pure policy reading the stats port; an `arq` worker maintains the rolling aggregates.
- **Accept:** integration test — after simulated degraded stats for a provider, the adaptive strategy shifts selection away from it; when stats recover, selection returns; `make check` green.

## Task 8 — Benchmarking harness
- [ ] A benchmarking capability (CLI `conduit bench`) that drives a configurable workload across providers/models and reports latency (p50/p95/p99), cost, throughput, and error rate as a table. Runs against mocked providers by default, with a `--live` flag for real backends.
- **Accept:** the benchmark runs against mocked providers and emits a coherent results table; `make check` green.

## Task 9 — Docs, final integration & exit
- [ ] Write **ADR-0008** (caching + dedup + semantic strategy + replay privacy + adaptive routing). Update `docs/ARCHITECTURE.md` (optimization/caching section) and the `README.md` feature list (add caching, dedup, cost prediction, adaptive routing, benchmarking; include any benchmark numbers). Update `CLAUDE.md` §10 to mark the roadmap complete and note post-V5 hardening as the next area.
- [ ] One end-to-end integration pass exercising cache → dedup → (semantic) → predict → adaptive routing together on a request path, with the compat gate re-run.
- **Accept:** the Phase 5 exit checklist below and the ROADMAP Phase 5 **DoD** hold; the OpenAI compatibility gate still passes; `make check` green.

---

### Phase 5 exit checklist
- [ ] Exact-match cache serves identical cacheable requests with no upstream call, for unary and streaming, with correct framing; bypass works.
- [ ] In-flight dedup collapses concurrent identical requests to a single upstream call.
- [ ] Semantic cache serves near-matches above threshold (mocked embedder in tests) and respects all safety guards.
- [ ] Replay capture is opt-in/off-by-default and redaction-safe; replay re-runs a captured request through the current pipeline.
- [ ] Cost prediction is exposed additively without changing the OpenAI-compatible chat contract.
- [ ] Adaptive routing shifts selection in response to changing cost/latency/error stats and recovers.
- [ ] The benchmarking harness produces a results table across providers/models.
- [ ] `make check` green; integration tests use real Redis + Postgres (testcontainers) with providers and the embedder mocked; a hit is proven to make no upstream call.
- [ ] OpenAI compatibility intact — the compat gate still passes (no regression).
- [ ] Docs (`ARCHITECTURE`, ADR-0008, `README`, `CLAUDE.md` §10) reflect reality.
