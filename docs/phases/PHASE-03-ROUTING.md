# Phase 3 — Smart Routing: Task Checklist

Concrete, ordered tasks for intelligent routing. Work top to bottom. Tick each box when its acceptance note is satisfied, and keep `CLAUDE.md` §10 current. Do not start Phase 4 until every box here is checked and the Phase 3 **Definition of Done** in `docs/ROADMAP.md` holds.

This phase fills the **route** stage (`CLAUDE.md` §5 step 4): it extends the existing `RoutingStrategy` protocol and `RoutingDecision` (Phase 1 Task 7) with cost-, latency-, and capability-aware strategies, plus per-key/org routing policies, and it fills the preflight **classification** seam (§5 step 3). It consumes Phase 2's usage/latency/health data and produces the ordered fallback plan that Phase 2's execute/fallback already walks.

Hard back-compat rule: an explicit, real model name (e.g. `gpt-4o-mini`) must keep routing deterministically to the same provider as in Phase 1. Smart routing and aliases are **additive** — they must never change the meaning of a request that names a concrete model, and must never regress the OpenAI compatibility gate.

Conventions: routing logic is **pure** and lives in `domain/routing/` (unit-tested with injected catalog/stats); policy persistence and end-to-end routing are integration-tested on testcontainers with providers mocked via `respx`. Record the routing model in **ADR-0006**. Justify any new dependency in an ADR/PR note. Every schema change ships an Alembic migration.

---

## Task 1 — Provider capability & pricing catalog
- [ ] Make the per-model metadata on `providers/base` authoritative and complete: context window, supported features (tools/function calling, JSON mode, vision), and input/output per-token pricing. Include a slot for observed latency (populated from Task 6's stats port).
- [ ] `domain/routing/catalog.py`: a pure catalog that lists candidate `(provider, model)` pairs and filters them by a set of capability requirements.
- **Accept:** unit tests — given requirements (min context length, tools, vision, JSON mode), the catalog returns exactly the capable candidates and excludes the rest; `make check` green.

## Task 2 — Request classification (fill the preflight classify seam)
- [ ] `domain/routing/classify.py`: pure derivation of a request's requirements from the canonical request — estimated prompt tokens (→ required context window), whether tools/functions are requested, whether image parts are present (vision), JSON/structured-output mode, and streaming. No I/O.
- [ ] Wire it into the preflight stage (the classification no-op seam from §5 step 3); the result feeds routing.
- **Accept:** unit tests map representative requests (plain chat, tool-call, vision, long-context, JSON mode) to the correct requirement set; `make check` green.

## Task 3 — Routing policies (persisted, per key/org)
- [ ] Alembic migration: `routing_policy` (scope = `org_id` or `api_key_id`; `objective` ∈ {cost, latency, balanced}; provider allowlist/denylist; optional model-class pins; fallback preferences; `status`). See `docs/ARCHITECTURE.md` §9.
- [ ] Load the applicable policy in the pipeline (key-level overrides org-level; a sane default when none is set).
- [ ] Admin endpoints under `api/v1/admin/` to create/list/update/inspect policies (guarded by the admin key).
- **Accept:** integration test — a stored policy is loaded and applied to routing (allowlist restricts candidates, objective selects the strategy); default policy applies when none exists; `make check` green.

## Task 4 — Model classes & aliases (cross-provider candidates) — ADR-0006
- [ ] `domain/routing/classes.py`: resolve a requested `model` to an ordered candidate set. A concrete provider model name resolves to itself (deterministic, back-compat). A **logical class/alias** (e.g. `fast`, `balanced`, `frontier`) resolves to a configured, capability-ranked candidate set spanning providers.
- [ ] Class/alias definitions come from config/DB, not hardcoded.
- **Accept:** unit tests — a concrete model → the exact same single candidate as Phase 1's static mapping; an alias → its ordered candidate set; an unknown model still yields the OpenAI-shaped `404` from Phase 1; `make check` green.

## Task 5 — Cost-optimized strategy
- [ ] `domain/routing/strategies/cost.py`: among candidates that satisfy the request's requirements and the policy's allow/deny rules, choose the lowest **estimated cost** (prompt tokens + expected completion tokens × per-token pricing from the catalog). Build the fallback plan in ascending cost order.
- **Accept:** unit tests — the cheapest capable+allowed candidate is chosen; incapable/denied candidates are excluded; ties break deterministically; the decision carries an ordered fallback plan; `make check` green.

## Task 6 — Latency-optimized strategy + a stats port
- [ ] `domain/routing/stats.py`: a `LatencyStats` port (pure interface) the domain reads; an `infra/` adapter implements it from Phase 2's rolling latency/health data (Redis/Postgres). No vendor/framework imports in the domain side.
- [ ] `domain/routing/strategies/latency.py`: choose the lowest observed latency among capable+allowed candidates; fallback plan by ascending latency; fall back to a neutral default when no stats exist yet.
- **Accept:** unit tests with injected stats — fastest capable candidate chosen; cold-start (no stats) behaves sanely; `make check` green.

## Task 7 — Balanced strategy & strategy selection
- [ ] `domain/routing/strategies/balanced.py`: combine normalized cost, latency, and capability fit into a single score with configurable weights.
- [ ] `domain/routing/engine.py`: select the active strategy from the policy `objective`; `StaticStrategy` remains the default and is always used for requests that name a concrete model.
- **Accept:** unit tests — each objective selects the right strategy; a concrete-model request bypasses to static regardless of objective; weights change the balanced outcome as expected; `make check` green.

## Task 8 — Pipeline integration, docs & exit
- [ ] The route stage now runs classify → load policy → resolve classes → run the selected strategy → emit a `RoutingDecision` (chosen `(provider, model)` + ordered fallback plan + reason). Confirm Phase 2's execute/breaker/fallback consumes the plan unchanged.
- [ ] Write **ADR-0006** (routing model: classes/aliases, strategies, back-compat guarantee). Update `docs/ARCHITECTURE.md` (routing section) and `CLAUDE.md` §10 to point at Phase 4.
- [ ] End-to-end integration test (respx, multiple providers): a cost policy routes to the cheapest capable provider; a latency policy to the fastest; a capability filter excludes an incapable provider; when the chosen provider fails, the smart fallback plan is walked (ties into Phase 2).
- **Accept:** the Phase 3 exit checklist below and the ROADMAP Phase 3 **DoD** hold; the OpenAI compatibility gate still passes; `make check` green.

---

### Phase 3 exit checklist
- [ ] Requests are classified (context/tools/vision/JSON/streaming) and routed by the active policy objective.
- [ ] Cost, latency, and balanced strategies all work behind the one `RoutingStrategy` protocol; the decision includes an ordered fallback plan.
- [ ] Capability filtering excludes providers that can't serve a request; policy allow/deny lists are honored.
- [ ] Concrete model names route deterministically (unchanged from Phase 1); aliases/classes resolve to candidate sets; unknown models still `404`.
- [ ] Smart routing integrates cleanly with Phase 2's retry/breaker/fallback and usage accounting.
- [ ] `make check` green; integration tests use real Postgres + Redis (testcontainers) with providers mocked.
- [ ] OpenAI compatibility intact — the compat gate still passes (no regression).
- [ ] Docs (`ARCHITECTURE`, ADR-0006, `CLAUDE.md` §10) reflect reality.
