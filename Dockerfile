# syntax=docker/dockerfile:1

# ---- build stage: resolve and install deps with uv ----
FROM python:3.12-slim AS build

# uv: fast, reproducible installs from uv.lock
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (cached layer), then the project.
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev || \
    uv sync --no-install-project --no-dev

# README.md is referenced by pyproject (readme = ...); it must exist to build the wheel.
COPY src ./src
COPY README.md ./README.md
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

# ---- runtime stage: slim, non-root ----
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    CONDUIT_HOST=0.0.0.0 \
    CONDUIT_PORT=8080 \
    CONDUIT_WORKERS=2

# Non-root runtime user
RUN groupadd --system conduit && useradd --system --gid conduit --home /app conduit

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY src ./src
# alembic.ini is created in Phase 1 Task 2; image builds (`make up`) begin at Task 11,
# by which point it exists. It is needed so the container can run `alembic upgrade head`.
COPY alembic.ini ./alembic.ini

USER conduit
EXPOSE 8080

# Liveness probe without needing curl in the image
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

# Prod server: gunicorn managing uvicorn workers.
CMD ["sh", "-c", "gunicorn conduit.main:app -k uvicorn.workers.UvicornWorker -b ${CONDUIT_HOST}:${CONDUIT_PORT} -w ${CONDUIT_WORKERS}"]
