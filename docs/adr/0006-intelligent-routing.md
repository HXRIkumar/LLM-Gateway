# ADR-0006 — Intelligent routing: classes, strategies & back-compat

- **Status:** Accepted
- **Date:** 2026-07-05
- **Deciders:** Core maintainers

## Context
Phase 1 routed by a static `model → provider` map. Phase 3 makes routing
*intelligent* — choosing among capable providers by cost, latency, or a balanced
score, under per-key/org policies — without breaking the single most important
promise: a request that names a concrete model must behave exactly as before.

## Decision

Routing stays behind the one `RoutingStrategy` interface (ADR-0003 / Phase 1
Task 7) and produces the same `RoutingDecision` (chosen provider/model + ordered
fallback plan + reason) that Phase 2's execute/breaker/fallback already consumes.
The route stage composes pure pieces:

1. **Classify** (`domain/routing/classify.py`) — derive capability requirements
   from the request (context size, tools, vision, JSON mode).
2. **Resolve classes** (`domain/routing/classes.py`) — turn the requested model
   into an ordered candidate set. **A concrete model resolves to itself** (its
   Phase-1 provider); a **logical alias** (`fast`, `frontier`, …) resolves to its
   configured, cross-provider candidate list; anything else is a `ModelNotFound`
   404. Concrete resolution is checked first, so aliases can never shadow a real
   model name.
3. **Filter** (`domain/routing/catalog.py`) — keep only candidates whose metadata
   satisfies the requirements, then apply the policy's provider allow/deny lists.
4. **Select** — run the strategy named by the policy `objective`:
   `cost` (cheapest capable), `latency` (fastest observed), or `balanced`
   (weighted score). The strategy emits the primary choice plus an ordered
   fallback plan; ties break deterministically.

**Back-compat guarantee (binding):** any request naming a concrete provider
model bypasses strategy selection entirely and uses the static mapping — the
`StaticStrategy` — so its routing is byte-for-byte the Phase-1 behaviour and the
OpenAI compatibility gate cannot regress. Smart routing is *additive*, reached
only through aliases/classes or an explicit policy over an alias.

Latency and other live signals are read through a **`LatencyStats` port**
(`domain/routing/stats.py`), implemented by an infra adapter over Phase 2's
rolling data — the domain strategies stay pure and framework-free. Aliases,
class definitions, and policies come from configuration/DB, never hardcoded.

## Consequences
- The routing decision is explainable (a recorded reason) and testable in
  isolation: classify, resolve, filter, and each strategy are pure functions.
- Concrete-model traffic is provably unchanged; the risk of smart routing is
  contained to opt-in aliases/policies.
- Adding a strategy is a new pure module + one `objective` value — the pipeline,
  execute layer, and providers are untouched.

## Alternatives considered
- **Route everything through the smart engine** (including concrete models) —
  rejected: it puts the compatibility guarantee at the mercy of scoring logic and
  live signals. Concrete names must be deterministic.
- **Latency/cost signals imported directly into the domain** — rejected: it would
  drag infra (Redis/Postgres) into `domain/`. A port keeps the core pure (ADR-0003).
