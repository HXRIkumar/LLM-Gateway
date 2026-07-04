"""Application configuration.

All runtime configuration is environment-driven with the ``CONDUIT_`` prefix and
read through a single :class:`Settings` object (pydantic-settings). Nothing reads
``os.getenv`` directly — the settings object is constructed once in the app
factory and injected everywhere it is needed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, environment-driven configuration for the gateway.

    Defaults target local development so ``make dev`` boots without a ``.env``.
    docker-compose overrides the datastore URLs with in-network service names.
    """

    model_config = SettingsConfigDict(
        env_prefix="CONDUIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime ---
    env: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 2

    # --- Datastores ---
    database_url: str = "postgresql+asyncpg://conduit:conduit@localhost:5432/conduit"
    redis_url: str = "redis://localhost:6379/0"

    # --- Admin / bootstrap ---
    # Guards the admin endpoints and the `conduit keys create` CLI.
    admin_api_key: SecretStr = SecretStr("change-me-generate-a-long-random-secret")

    # --- Providers ---
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_base_url: str = "http://localhost:11434"

    # --- Routing ---
    # Explicit model → provider overrides for static routing. Empty by default:
    # routes are derived from each provider's advertised models, and entries here
    # add or override them (e.g. {"gpt-4o-mini": "openai"}). Parsed from JSON when
    # set via CONDUIT_MODEL_ROUTES.
    model_routes: dict[str, str] = Field(default_factory=dict)
    # Ordered fallback model ids per requested model (each resolved via model_routes).
    # e.g. {"gpt-4o-mini": ["llama3.2"]}. Parsed from JSON via CONDUIT_MODEL_FALLBACKS.
    model_fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    # Logical aliases → ordered candidate model ids spanning providers (Phase 3).
    # e.g. {"fast": ["gpt-4o-mini", "llama3.2"]}. Via CONDUIT_MODEL_ALIASES (JSON).
    model_aliases: dict[str, list[str]] = Field(default_factory=dict)

    # --- Rate limiting (Phase 2) ---
    rate_limit_enabled: bool = True
    rate_limit_per_key_requests: int = 60
    rate_limit_per_key_window_seconds: float = 60.0
    rate_limit_per_org_requests: int = 600
    rate_limit_per_org_window_seconds: float = 60.0

    # --- Retry (Phase 2) ---
    retry_max_attempts: int = 3
    retry_base_delay_seconds: float = 0.1
    retry_max_delay_seconds: float = 5.0

    # --- Circuit breaker (Phase 2) ---
    breaker_enabled: bool = True
    breaker_failure_threshold: int = 5
    breaker_cooldown_seconds: float = 30.0

    # --- Observability (Phase 4) ---
    # Tracing is enabled only when an OTLP endpoint is set; otherwise a clean no-op.
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "conduit"
    metrics_enabled: bool = True

    # --- HTTP client ---
    # Timeout (seconds) applied to the shared outbound httpx client.
    request_timeout_seconds: float = 60.0

    @property
    def is_production(self) -> bool:
        """True when running in the production profile (drives log rendering, etc.)."""
        return self.env == "production"
