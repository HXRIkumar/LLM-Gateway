# Conduit — task runner. `make help` lists targets.
# All commands assume `uv` is installed: https://docs.astral.sh/uv/

HOST ?= 0.0.0.0
PORT ?= 8080
APP  := conduit.main:app

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Sync deps (incl. dev) and install pre-commit hooks
	uv sync --all-groups
	uv run pre-commit install

.PHONY: dev
dev: ## Run the API with autoreload
	uv run uvicorn $(APP) --reload --host $(HOST) --port $(PORT)

.PHONY: up
up: ## Start the stack (api + postgres + redis)
	docker compose up --build -d

.PHONY: up-observability
up-observability: ## Start the stack + observability profile (prometheus, grafana, otel)
	docker compose --profile observability up --build -d

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: logs
logs: ## Tail api logs
	docker compose logs -f api

.PHONY: migrate
migrate: ## Apply DB migrations
	uv run alembic upgrade head

.PHONY: revision
revision: ## Create a migration: make revision m="message"
	uv run alembic revision --autogenerate -m "$(m)"

.PHONY: key
key: ## Mint an initial API key (admin CLI)
	uv run conduit keys create

.PHONY: worker
worker: ## Run the arq background worker (health probes, usage rollups)
	uv run arq conduit.workers.settings.WorkerSettings

.PHONY: test
test: ## Run all tests (unit + integration)
	uv run coverage run -m pytest
	uv run coverage report

.PHONY: test-unit
test-unit: ## Run fast unit tests only (no containers)
	uv run pytest tests/unit -m "not integration"

.PHONY: lint
lint: ## Lint with ruff
	uv run ruff check .

.PHONY: fmt
fmt: ## Format with ruff
	uv run ruff format .

.PHONY: types
types: ## Type-check with mypy (strict)
	uv run mypy src

.PHONY: check
check: lint types test ## Everything CI runs — green before every commit

.PHONY: clean
clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
