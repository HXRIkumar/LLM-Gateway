# ADR-0003 — Canonical schema & provider abstraction

- **Status:** Accepted
- **Date:** 2026-01-05
- **Deciders:** Core maintainers

## Context
Conduit must sit in front of many providers (OpenAI, Ollama now; Anthropic, Gemini, Bedrock, vLLM later) while exposing one stable API to applications. Two shapes were possible: (a) a thin passthrough that forwards each provider's own request/response format, or (b) one canonical internal schema with per-provider translation. Routing, retries, fallback, cost accounting, and caching all need to reason about requests uniformly — that only works if the core sees a single shape.

## Decision
Adopt a **single canonical schema that is OpenAI-compatible** (`domain/schemas.py`) as the internal lingua franca and the public wire format. Every provider is a **`Provider` adapter** implementing an async protocol (`providers/base.py`) that translates canonical ⇄ provider-native and maps provider errors onto `domain.errors`. Adapters are discovered through a registry (`providers/registry.py`); adding a provider is implementing the protocol plus one registry entry — no changes to routing, services, or the edge.

The public API stays OpenAI-compatible so a stock OpenAI SDK works unmodified against Conduit. Breaking that compatibility requires a new ADR.

The `Provider` protocol includes: identity + advertised models with capability/pricing metadata (context window, tools, JSON mode, vision, per-token cost), `chat_completion`, `stream_chat_completion`, and `health`.

## Consequences
- The routing engine, reliability layer, cost accounting, and caching are all **provider-agnostic** — they operate on the canonical schema and capability metadata.
- Vendor quirks and SDK details are quarantined inside adapters; nothing provider-specific leaks into `domain/`, `services/`, or `api/`.
- New providers are cheap and low-risk to add, which is central to the extensibility goal and to attracting community contributions.
- We take on translation cost and must keep adapters faithful — mitigated by per-adapter contract tests against recorded fixtures.
- Capabilities beyond the OpenAI surface (provider-specific parameters) need an explicit, documented extension mechanism rather than ad-hoc passthrough.

## Alternatives considered
- **Thin passthrough per provider** — simplest to start, but makes uniform routing/cost/caching essentially impossible and pushes provider differences onto every application. Rejected.
- **A bespoke non-OpenAI canonical format** — cleaner in theory, but throws away drop-in SDK compatibility, the single biggest adoption lever. Rejected in favor of OpenAI-compatible.
