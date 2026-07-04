# ADR-0002 — Language, runtime, and web framework

- **Status:** Accepted
- **Date:** 2026-01-05
- **Deciders:** Core maintainers

## Context
Conduit is an LLM gateway: an async, streaming, OpenAI-compatible HTTP service that must integrate with many provider APIs and reason about routing, cost, tokens, and (later) embeddings for semantic caching. Two runtimes were serious candidates: **Go** (the language of Kong/Envoy-style proxies — excellent concurrency, single-binary deploys, low latency) and **Python** (the native language of the AI ecosystem, with FastAPI as a first-class async web framework and the closest analog, LiteLLM, written in it).

## Decision
Build Conduit in **Python 3.12+, async-first, on FastAPI**, with **uv** for dependency/lockfile management, **uvicorn** in dev and **gunicorn + uvicorn workers** in prod. Isolate the request-critical path behind clean seams (the `services/` pipeline and `providers/` adapters) so a future high-throughput component could be re-implemented in a faster runtime without reshaping the system.

## Rationale
- **Ecosystem fit.** Provider SDKs, tokenizers, embedding libraries, and OTel/Prometheus clients are mature and idiomatic in Python. The intelligence layers (routing signals, token estimation, semantic caching) are far cheaper to build here.
- **Delivery speed.** FastAPI + Pydantic v2 gives validated, OpenAI-shaped schemas and native async streaming with little ceremony — the MVP ships fast and stays readable, which matters for an open-source project meant to attract contributors.
- **Adequate performance.** The gateway's latency budget is dominated by upstream provider calls (hundreds of ms to seconds). A well-written async Python service with pooled `httpx` and Redis fast-path state is not the bottleneck; provider round-trips are.
- **Contributor pool.** The target audience (AI application engineers) overwhelmingly writes Python, lowering the barrier to community adapters and policies.

## Consequences
- Raw per-request overhead is higher than a compiled Go proxy; we accept this because upstream latency dominates and horizontal scaling is straightforward (stateless replicas + shared Redis/Postgres).
- We must be disciplined about async correctness (no blocking I/O on the request path) and keep vendor code quarantined so the hot path stays isolatable.
- If profiling later shows the proxy layer itself is a bottleneck at scale, a targeted Go/Rust data-plane component is an option — enabled by, not blocked by, this decision.

## Alternatives considered
- **Go** — best pure-proxy performance and ops story, but weaker AI-ecosystem fit and slower to build the intelligence layers; higher barrier for the likely contributor base. Rejected for v1; retained as a future data-plane option.
- **Node/TypeScript** — good async and streaming, decent ecosystem, but weaker for token/embedding/ML tooling than Python. Rejected.
