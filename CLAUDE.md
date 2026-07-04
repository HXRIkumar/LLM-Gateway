# CLAUDE.md — Operating Manual for Claude Code

> Read this file at the start of **every** session before doing anything else.
> It is the contract for how work happens in this repository. When in doubt,
> this file and the documents it points to win over your own assumptions.

Project codename: **Conduit** *(placeholder — see ADR-0002; verify name availability on PyPI/GitHub/npm before any public release).*

---

## 1. What this is (and is not)

Conduit is a **production-grade, open-source AI Gateway**: the control plane that
sits between applications and multiple LLM providers (OpenAI, Anthropic, Gemini,
Ollama, and future models). Every request goes through the gateway, which decides
routing, retries, fallback, caching, budget, rate limits, logging, and tracing.

Mental model: **NGINX / Kong / Envoy, but for LLM traffic** — with the cost
controls of Stripe and the observability of LangSmith.

**This is infrastructure.** It is **not** a chatbot, an assistant, a prompt
playground, a LangChain demo, or a RAG app. If a change starts to look like any of
those, stop and re-read the mission in `docs/ROADMAP.md`.

The single most important user-facing promise: **the API is OpenAI-compatible.**
A client that points its OpenAI SDK `base_url` at Conduit must work unmodified.
Never break that contract without an ADR.

---

## 2. Golden rules

1. **Build incrementally, one phase at a time.** Follow `docs/ROADMAP.md` in order.
   Do not build V3 routing before the V2 reliability layer exists. Do not start a
   phase until the previous phase meets its Definition of Done.
2. **Every feature is production-worthy or it does not merge.** No `TODO: handle
   errors later`, no swallowed exceptions, no happy-path-only code.
3. **Architecture over shortcuts.** Prefer a clean seam now over a rewrite later.
   The layering in §4 is not optional.
4. **Every abstraction must earn its place** (ADR-0001). If you introduce an
   interface, a base class, or a new dependency, there must be a concrete reason —
   record it in an ADR when it is a real decision.
5. **Every component is independently testable.** If you cannot unit-test a piece
   without spinning up the whole app, the seam is wrong.
6. **Keep the docs true.** When you change architecture, decisions, or the plan,
   update `docs/ARCHITECTURE.md`, the relevant ADR, and the status block in §9 in
   the *same* change. Stale docs are a bug.
7. **Do not guess dependencies or config.** Pin what §3 specifies. Ask (in a
   commit note / PR description) before adding anything not listed.
8. **Secrets never touch the repo, logs, or traces.** See §7.

---

## 3. Pinned technology stack

These are decided (see the ADRs). Do **not** swap them without a new ADR.

| Concern | Choice | Notes |
|---|---|---|
| Language / runtime | **Python 3.12+** | async-first (ADR-0002) |
| Package / env manager | **uv** | `uv.lock` is committed; use `uv add`, never edit deps by hand |
| Web framework | **FastAPI** | ASGI, Pydantic-native |
| ASGI server | **uvicorn** (dev), **gunicorn + uvicorn workers** (prod) | |
| Validation / models | **Pydantic v2** + **pydantic-settings** | all config via settings, no `os.getenv` scattered |
| HTTP client (providers) | **httpx** (async) | one shared client, connection-pooled; `respx` for tests |
| Relational store | **PostgreSQL 16** | system of record (ADR-0004) |
| ORM / migrations | **SQLAlchemy 2.0 (async)** + **asyncpg** + **Alembic** | |
| Cache / limits / breaker state | **Redis 7** (`redis-py` async) | ephemeral, fast-path state (ADR-0004) |
| Background workers | **arq** (Redis-native, async) | introduced in V2; do not add Celery |
| Logging | **structlog** | structured JSON in prod, pretty in dev |
| Tracing / metrics | **OpenTelemetry SDK** + OTLP exporter; **prometheus-client** | introduced fully in V4, seams from day one |
| Tests | **pytest**, **pytest-asyncio**, **testcontainers**, **respx**, **coverage** | |
| Lint / format | **ruff** (lint + format) | one tool; no black/isort/flake8 |
| Types | **mypy** (strict) | new code must type-check clean |
| Pre-commit | **pre-commit** | ruff + mypy + basic hygiene |
| Containerization | **Docker** (multi-stage) + **docker-compose** | K8s/Helm is a later concern |

Canonical ports (keep consistent across compose, docs, and code):
`API 8080 · Postgres 5432 · Redis 6379 · Prometheus 9090 · Grafana 3000 · OTel collector 4317/4318`.

All runtime config is environment-driven with the prefix **`CONDUIT_`** (see
`.env.example`).

---

## 4. Architecture in one screen

Hexagonal / ports-and-adapters. Dependencies point **inward**. The domain core
imports nothing framework- or vendor-specific.

```
            HTTP (OpenAI-compatible)
                     │
   ┌─────────────────▼──────────────────┐
   │  api/         edge layer            │  FastAPI routers, middleware,
   │               (framework-facing)    │  auth, request→schema, error envelope
   └─────────────────┬──────────────────┘
                     │ calls
   ┌─────────────────▼──────────────────┐
   │  services/    orchestration         │  the request pipeline; wires
   │               (use cases)           │  domain + providers + infra
   └───────┬─────────────────┬──────────┘
           │                 │
   ┌───────▼──────┐   ┌───────▼─────────┐
   │  domain/     │   │  providers/     │  adapters that IMPLEMENT domain
   │  pure core   │   │  (adapters)     │  protocols: openai, ollama, ...
   │  schemas,    │   └───────┬─────────┘
   │  routing,    │           │
   │  reliability │   ┌───────▼─────────┐
   │  (no I/O)    │   │  infra/         │  db, redis, telemetry, config —
   └──────────────┘   │  (adapters)     │  the only place that does I/O
                      └─────────────────┘
                              │
                    Postgres · Redis · OTLP
```

Rules that follow from this:
- `domain/` must not import `fastapi`, `sqlalchemy`, `httpx`, `redis`, or any
  provider SDK. It defines **protocols**; others implement them.
- `api/` never talks to a provider or a database directly. It calls `services/`.
- Adding a provider = implement `providers/base.Provider` + register it. Nothing
  else in the system changes. This is the extensibility test (ADR-0003).
- Reliability primitives (retry, circuit breaker, fallback) are **policies in the
  domain**; their *state* lives in Redis via an infra adapter.

Target package layout (build into this; create dirs as phases require them):

```
src/conduit/
├── main.py            # app factory + lifespan (startup/shutdown wiring)
├── config.py          # Settings (pydantic-settings)
├── api/
│   ├── errors.py      # OpenAI-shaped error envelope + exception handlers
│   ├── middleware.py  # request-id, timing, structured access logs, auth
│   ├── deps.py
│   └── v1/
│       ├── chat.py    # POST /v1/chat/completions (+ SSE streaming)
│       ├── models.py  # GET  /v1/models
│       └── admin/     # key management, health, (later) budgets/policies
├── domain/
│   ├── schemas.py     # canonical OpenAI-compatible request/response types
│   ├── errors.py      # domain exceptions (mapped to HTTP in api/errors.py)
│   ├── routing/       # engine.py, strategy.py (RoutingStrategy protocol), decision.py
│   └── reliability/   # retry.py, breaker.py, fallback.py (pure policies) [V2]
├── providers/
│   ├── base.py        # Provider protocol + capability/model metadata
│   ├── registry.py    # name -> provider factory
│   ├── openai.py
│   └── ollama.py
├── services/
│   ├── gateway.py     # THE request pipeline (see §5)
│   ├── keys.py        # API key issuance + verification
│   └── usage.py       # usage/token accounting [V2]
├── infra/
│   ├── db/            # engine, session, models, alembic/
│   ├── redis.py
│   └── telemetry/     # logging.py, tracing.py, metrics.py
├── workers/           # arq worker defs: health checks, usage rollups [V2]
└── cli.py             # optional admin CLI (create key, run migrations, etc.)
tests/
├── unit/              # pure, fast, no containers
├── integration/       # testcontainers: real Postgres + Redis, mocked providers
└── conftest.py
```

Full design, diagrams, and the data model live in **`docs/ARCHITECTURE.md`**.

---

## 5. The request pipeline (the heart of the system)

`services/gateway.py` orchestrates every request in this order. Build the seams in
the MVP even where a stage is a no-op until a later phase.

1. **Authenticate** — resolve the API key to a principal/org. Reject early.
2. **Validate + normalize** — parse into the canonical `domain` schema. Reject
   malformed input with an OpenAI-shaped error.
3. **Preflight policy** — budget check (V2), rate limit (V2), request
   classification (V3). Reject with `429`/`402`-style errors when tripped.
4. **Route** — the routing engine returns a `RoutingDecision`: chosen provider +
   model, plus a fallback plan. MVP = explicit `model → provider` mapping;
   V3 = cost/latency/capability-aware strategies behind the same interface.
5. **Execute** — call the provider adapter. Streaming and non-streaming share the
   normalization layer. V2 wraps this in retry + circuit breaker, and walks the
   fallback plan on failure.
6. **Account + observe** — record usage/tokens/cost, emit trace spans, write a
   structured access log. V5 adds semantic cache read/write around execution.
7. **Respond** — return an OpenAI-compatible body, or an SSE stream of
   `chat.completion.chunk` events terminated by `data: [DONE]`.

Each numbered stage maps to something in the roadmap. Keep the stage boundaries
clean so later phases slot in without rewrites.

---

## 6. How to work — the loop

For every unit of work:

1. **Read** `docs/ROADMAP.md` and the current phase file in `docs/phases/` to find
   the next unchecked task. Work top-to-bottom; don't skip ahead.
2. **Design the seam** if the task introduces one. Prefer a protocol in `domain/`
   with an adapter elsewhere.
3. **Write the test first** (or alongside). Unit tests for domain logic;
   integration tests (testcontainers) for anything touching Postgres/Redis;
   `respx` to mock provider HTTP. No feature is done without tests.
4. **Implement** the smallest change that satisfies the test and the spec.
5. **Verify locally**: `make check` (lint + types + tests) must pass. `make up`
   must still boot the stack.
6. **Update docs**: architecture/ADR/status if anything changed.
7. **Commit** with a Conventional Commit message (§8). One logical change per
   commit. Tick the task box in the phase file.

Never leave the tree broken between commits. If a change is large, land it as a
sequence of green commits, not one giant red one.

---

## 7. Guardrails — hard "do nots"

- **Do not** break OpenAI API compatibility (request shape, response shape, error
  envelope, streaming format) without an ADR.
- **Do not** call a provider SDK/HTTP endpoint from `api/` or `domain/`. Only
  `providers/` talks to providers; only `infra/` does other I/O.
- **Do not** put secrets, API keys, tokens, or full prompt bodies into logs,
  traces, metric labels, error messages, or URLs/query strings. Log an API key by
  its hashed prefix only. Redact provider credentials everywhere.
- **Do not** store provider API keys in plaintext in the DB. Hash gateway-issued
  keys; encrypt or externalize upstream provider credentials.
- **Do not** add a dependency that isn't in §3 without justifying it (ADR or PR
  note). Avoid unnecessary frameworks — the mission is explicit about this.
- **Do not** introduce global mutable state or module-level clients that can't be
  swapped in tests. Wire dependencies through the app factory / DI.
- **Do not** skip migrations. Every schema change ships an Alembic migration.
- **Do not** merge with failing `make check` or with skipped/`xfail` tests that
  hide real breakage.
- **Do not** hardcode config values; read them from `Settings`.

---

## 8. Conventions (summary — full detail in `docs/CONVENTIONS.md`)

- **Commits:** Conventional Commits — `feat:`, `fix:`, `refactor:`, `test:`,
  `docs:`, `chore:`, `perf:`, `build:`, `ci:`. Scope optional: `feat(providers): add ollama adapter`.
- **Branches:** `feat/<short-slug>`, `fix/<short-slug>`. Keep PRs small and phase-scoped.
- **Errors:** every error the client can see is an OpenAI-shaped envelope
  (`{"error": {"message", "type", "param", "code"}}`), produced centrally in
  `api/errors.py` from `domain` exceptions.
- **Async everywhere** on the request path. No blocking I/O in async handlers
  (no `requests`, no sync DB calls).
- **Typing:** public functions are fully annotated; `mypy --strict` clean.
- **Naming:** modules and packages are nouns for things, verbs for actions;
  provider adapters are named after the provider (`openai.py`, `ollama.py`).
- **Tests mirror source**: `tests/unit/<pkg>/test_<module>.py`.

---

## 9. Commands

The `Makefile` is the source of truth for tasks. Common ones:

```
make install     # uv sync + install pre-commit hooks
make up          # docker compose up: api + postgres + redis (+ observability profile)
make down        # stop the stack
make dev         # run the API with autoreload (uvicorn)
make migrate     # alembic upgrade head
make revision m="msg"   # create an alembic revision
make test        # pytest (unit + integration)
make test-unit   # fast unit tests only
make lint        # ruff check
make fmt         # ruff format
make types       # mypy --strict
make check       # lint + types + test  (run before every commit)
```

If a command you need doesn't exist, add a Make target rather than documenting a
raw one-off invocation.

---

## 10. Current status — START HERE

> Keep this block current. It is how a fresh session knows where the build is.

- **Active phase:** Phase 1 — MVP (`docs/phases/PHASE-01-MVP.md`)
- **State:** Tasks 1–5 complete — skeleton/config/health; SQLAlchemy + Alembic +
  Redis; pure domain core; provider abstraction + registry; and the OpenAI
  adapter (`providers/openai.py`): thin canonical ⇄ OpenAI translation for unary
  + streaming over the shared httpx client, with upstream errors mapped to
  `domain.errors` (auth/rate-limit/timeout/invalid/5xx). respx tests cover
  success, error mapping, timeouts, and SSE streaming. `make check` green.
- **Immediate next action:** Phase 1, Task 6 — Ollama provider adapter
  (`providers/ollama.py`): canonical ⇄ Ollama translation (unary + streaming),
  error mapping, base URL from settings; respx tests mirroring the OpenAI ones,
  proving the same canonical schema works unchanged across a different backend.

When you finish a task, tick its box in the phase file and update this block. When
you finish a phase, update the "Active phase" line and confirm the previous phase
meets its Definition of Done in `docs/ROADMAP.md`.

---

## 11. Where to look

- **`docs/ROADMAP.md`** — the mission, non-goals, and the phased plan (MVP→V5) with
  per-phase Definition of Done. Read this to know *what* to build and *in what order*.
- **`docs/ARCHITECTURE.md`** — the system design: components, request lifecycle,
  data model, provider contract, reliability & observability strategy, C4 + Mermaid diagrams.
- **`docs/CONVENTIONS.md`** — coding standards, testing strategy, git workflow, error format.
- **`docs/adr/`** — the *why* behind the big decisions. Read before contradicting one; add a new ADR to change one.
- **`docs/phases/`** — concrete, ordered task checklists per phase.
