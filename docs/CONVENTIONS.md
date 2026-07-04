# Conduit — Engineering Conventions

The detailed reference behind `CLAUDE.md` §8. If a rule here ever conflicts with `CLAUDE.md`, `CLAUDE.md` wins and this file should be corrected.

## Code style & structure

- **Python 3.12+, async-first.** No blocking I/O on the request path — no `requests`, no sync DB drivers, no `time.sleep`. Use `httpx.AsyncClient`, async SQLAlchemy, async redis.
- **One tool per job.** Formatting and linting are both **ruff** (`ruff format`, `ruff check`). Do not add black, isort, or flake8.
- **Typing is mandatory.** All public functions and methods are fully annotated. `mypy --strict` must pass on new code. Prefer `Protocol` for ports; avoid `Any` — justify it in a comment if unavoidable.
- **Pydantic v2** for all external data (requests, responses, config). Domain schemas live in `domain/schemas.py` and are the single canonical shape; provider-native shapes never escape `providers/`.
- **Config only via `Settings`** (`pydantic-settings`, prefix `CONDUIT_`). No `os.getenv` sprinkled through modules.
- **No global mutable singletons.** Construct clients (DB engine, redis, httpx, providers) in the app factory / lifespan and inject them. Anything used on the request path must be replaceable in a test.
- **Small modules, clear names.** A file does one thing. Provider adapters are named after the provider (`openai.py`, `ollama.py`). Functions are verbs, types are nouns.
- **Docstrings** on public modules, classes, and non-obvious functions — say *why*, not *what the code already says*.

## Error handling & API compatibility

- The public API is **OpenAI-compatible**. Preserve request/response shapes, streaming format (`data:` SSE lines, `chat.completion.chunk` objects, terminating `data: [DONE]`), and the error envelope. Breaking any of these requires an ADR.
- Client-visible errors use the OpenAI envelope, produced **centrally** in `api/errors.py`:
  ```json
  {"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": null}}
  ```
- Internally, raise typed `domain.errors` exceptions (e.g. `AuthError`, `ValidationError`, `BudgetExceeded`, `RateLimited`, `ProviderTimeout`, `ProviderRateLimited`, `AllProvidersFailed`). Exception handlers map these to status codes + envelopes. Handlers never leak stack traces, secrets, or full prompt bodies.
- No bare `except:`; no swallowing exceptions; no returning `None` to signal an error on the request path.

## Testing strategy

- **Test pyramid.** Many fast unit tests over pure `domain/` logic; a focused set of integration tests over the wired system.
- **Unit tests** (`tests/unit/`) touch no network, DB, or containers. Domain routing, reliability policies, schema validation, cost math — all unit-tested in isolation.
- **Integration tests** (`tests/integration/`) use **testcontainers** for a real Postgres and Redis, and **respx** to simulate provider HTTP — including success, timeout, 429, 5xx, malformed bodies, and mid-stream failures. Exercise the full pipeline through the FastAPI app via `httpx.AsyncClient`.
- **Compatibility tests** (Phase 1 DoD): drive Conduit with the actual OpenAI SDK (`base_url` pointed at the test app) for both unary and streaming calls.
- Tests mirror source layout: `tests/unit/<pkg>/test_<module>.py`.
- No feature merges without tests. Do not use `skip`/`xfail` to hide real breakage. Keep coverage meaningful (assert behavior, not lines) and aim high on `domain/` and `services/`.
- Tests must be deterministic and independent (no shared order dependence, no reliance on wall-clock or live network).

## Git & workflow

- **Conventional Commits:** `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `build`, `ci`. Optional scope: `feat(providers): add ollama adapter`. Imperative mood, present tense.
- One logical change per commit; the tree is green at every commit (`make check` passes).
- **Branches:** `feat/<slug>`, `fix/<slug>`, `docs/<slug>`. Keep PRs small and scoped to a single roadmap task where possible.
- **PR description** states: what changed, which roadmap task/phase it advances, how it was tested, and any new dependency or decision (link the ADR).
- Update docs in the same change as the behavior they describe. Tick the corresponding box in the phase file and refresh `CLAUDE.md` §10 when status moves.
- **pre-commit** runs ruff + mypy + basic hygiene; do not bypass it.

## Dependencies

- Managed with **uv**; `uv.lock` is committed. Add deps with `uv add <pkg>` — never hand-edit `pyproject.toml` deps or the lockfile.
- The stack in `CLAUDE.md` §3 is fixed. Any new dependency needs a justification in the PR (and an ADR if it's a real architectural choice). Bias toward the standard library and the already-chosen tools; "avoid unnecessary frameworks" is a project constraint, not a suggestion.

## Security hygiene

- Never commit secrets; `.env` is git-ignored and `.env.example` documents the variables with placeholder values.
- Log gateway keys only by hashed prefix. Never log or trace full keys, provider credentials, prompts, or completions.
- Never place secrets or personal data in URLs, query strings, metric labels, or span attributes.
- Store gateway-issued keys hashed; keep upstream provider credentials out of the database in plaintext.
