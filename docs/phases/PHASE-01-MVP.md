# Phase 1 — MVP: Task Checklist

Concrete, ordered tasks for the MVP. Work top to bottom. Tick each box when its acceptance note is satisfied, and keep `CLAUDE.md` §10 current. Do not start Phase 2 until every box here is checked and the Phase 1 **Definition of Done** in `docs/ROADMAP.md` holds.

Conventions: every task ships with tests and leaves `make check` green. Build into the layout in `CLAUDE.md` §4.

---

## Task 1 — Project skeleton & configuration
- [x] Create `src/conduit/` package with the module layout from `CLAUDE.md` §4 (empty `__init__.py` files; create sub-packages as later tasks need them).
- [x] `config.py`: `Settings` via `pydantic-settings`, prefix `CONDUIT_`, covering env, log level, host/port, `DATABASE_URL`, `REDIS_URL`, provider config (OpenAI key, Ollama base URL), bootstrap admin key. Sensible defaults for local dev.
- [x] `main.py`: FastAPI **app factory** + lifespan that constructs and tears down shared clients (DB engine, redis, httpx). No module-level singletons.
- [x] `infra/telemetry/logging.py`: structlog setup (JSON in prod, pretty in dev); request-id middleware in `api/middleware.py`.
- [x] Health endpoints: `GET /healthz` (liveness) and `GET /readyz` (checks DB + redis reachability).
- **Accept:** `make dev` boots; `/healthz` returns 200; `/readyz` reflects real dependency state; `make check` green.

## Task 2 — Datastore foundation
- [x] `infra/db/`: async SQLAlchemy 2.0 engine + session management (asyncpg); dependency-injected sessions.
- [x] Alembic initialized under `infra/db/alembic/`; `make migrate` / `make revision` wired.
- [x] First migration: `organization`, `api_key` (with `key_hash`, `prefix`, `status`) tables (see `docs/ARCHITECTURE.md` §9).
- [x] `infra/redis.py`: async redis client wired into lifespan.
- **Accept:** migrations apply to the compose Postgres; integration test (testcontainers) opens a session and round-trips a row; `make check` green.

## Task 3 — Canonical schemas (domain)
- [x] `domain/schemas.py`: OpenAI-compatible `ChatCompletionRequest`, `ChatCompletionResponse`, `ChatCompletionChunk`, message/role/usage types — the single canonical shape.
- [x] `domain/errors.py`: typed exception hierarchy (`AuthError`, `ValidationError`, `NotFound`, `ProviderError` + subtypes, `AllProvidersFailed`, …).
- **Accept:** unit tests validate parsing/serialization and round-trips against real OpenAI request/response fixtures; `make check` green.

## Task 4 — Provider abstraction & registry
- [x] `providers/base.py`: `Provider` async protocol + capability/model metadata (context window, tools, JSON mode, vision, per-token pricing) per ADR-0003.
- [x] `providers/registry.py`: `name → provider factory`, constructed from `Settings` in the app factory.
- **Accept:** a fake in-memory provider implements the protocol and is exercised end-to-end in tests; adding it required only an implementation + a registry entry; `make check` green.

## Task 5 — OpenAI provider adapter
- [x] `providers/openai.py`: canonical ⇄ OpenAI translation for unary and streaming; error mapping onto `domain.errors`; uses the shared async httpx client.
- **Accept:** `respx`-mocked tests cover success, 400/401/429/5xx mapping, timeouts, and a streamed response; `make check` green.

## Task 6 — Ollama provider adapter
- [x] `providers/ollama.py`: canonical ⇄ Ollama translation (unary + streaming); error mapping; base URL from settings.
- **Accept:** `respx`-mocked tests mirror Task 5 for Ollama's API shape; the same canonical schema works unchanged across both providers; `make check` green.

## Task 7 — Static routing seam
- [x] `domain/routing/strategy.py`: `RoutingStrategy` protocol + `RoutingDecision` (chosen provider/model + fallback plan + reason).
- [x] `domain/routing/engine.py`: `StaticStrategy` — explicit `model → provider` mapping from config. This is the seam V3 will fill.
- **Accept:** unit tests assert requests map to the configured provider/model and produce a decision object; `make check` green.

## Task 8 — API key management & authentication
- [ ] `services/keys.py`: issue (return the plaintext key once; store only the hash + prefix), verify (lookup by prefix, constant-time hash compare), list, revoke.
- [ ] `api/middleware.py`: bearer-token auth resolving the key to a principal; reject missing/invalid keys with an OpenAI-shaped `401`.
- [ ] Admin endpoints under `api/v1/admin/` to create/list/revoke keys (guarded by the bootstrap admin key). `cli.py` command to mint the first key.
- **Accept:** integration tests cover issue → authenticate → revoke → rejected; keys never stored or logged in plaintext; `make check` green.

## Task 9 — The gateway pipeline (unary)
- [ ] `services/gateway.py`: implement stages authenticate → validate → (preflight no-op) → route → execute → (account no-op) → respond, per `CLAUDE.md` §5.
- [ ] `api/v1/chat.py`: `POST /v1/chat/completions` (non-streaming) delegating to the pipeline.
- [ ] `api/v1/models.py`: `GET /v1/models` from the registry's advertised models.
- [ ] `api/errors.py`: central exception handlers mapping `domain.errors` → OpenAI envelopes + status codes.
- **Accept:** a non-streaming chat request succeeds against both OpenAI and Ollama (mocked) by changing only `model`; malformed input → OpenAI-shaped `400`; `make check` green.

## Task 10 — Streaming
- [ ] Streaming path through the pipeline and adapters: `POST /v1/chat/completions` with `stream: true` returns SSE `chat.completion.chunk` events terminated by `data: [DONE]`.
- **Accept:** integration test asserts correct SSE framing and termination for both providers (mocked); `make check` green.

## Task 11 — Containerization & one-command run
- [ ] Multi-stage `Dockerfile` (uv-based build; slim runtime; non-root user; healthcheck).
- [ ] `docker-compose.yml`: `api` + `postgres` + `redis` with healthchecks and an env file; migrations run on startup.
- [ ] `Makefile` targets from `CLAUDE.md` §9 all functional.
- **Accept:** `make up` yields a healthy stack reachable on `:8080`; a stock OpenAI SDK pointed at it completes unary + streamed calls; `make check` green.

## Task 12 — Compatibility gate & README quickstart
- [ ] Compatibility tests using the real OpenAI SDK against the running app (unary + streaming).
- [ ] Fill in the `README.md` Quickstart with the exact `docker compose up` + SDK snippet that works.
- **Accept:** compatibility tests pass; a new user can follow the README from clone to first successful request; Phase 1 **DoD** in `docs/ROADMAP.md` is fully satisfied. Update `CLAUDE.md` §10 to point at Phase 2.

---

### Phase 1 exit checklist
- [ ] All tasks above checked.
- [ ] Stock OpenAI SDK works against OpenAI **and** Ollama, unary **and** streaming, changing only `model`.
- [ ] Auth rejects bad keys (`401`); validation rejects bad input (`400`); both OpenAI-shaped.
- [ ] `docker compose up` → healthy; `/healthz` + `/readyz` accurate.
- [ ] `make check` green; integration tests use real Postgres + Redis (testcontainers) with providers mocked.
- [ ] Docs (`ARCHITECTURE`, ADRs, `CLAUDE.md` §10) reflect reality.
