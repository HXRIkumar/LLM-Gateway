"""Application configuration.

All runtime configuration is environment-driven with the ``CONDUIT_`` prefix and
read through a single :class:`Settings` object (pydantic-settings). Nothing reads
``os.getenv`` directly — the settings object is constructed once in the app
factory and injected everywhere it is needed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
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

    # --- HTTP client ---
    # Timeout (seconds) applied to the shared outbound httpx client.
    request_timeout_seconds: float = 60.0

    @property
    def is_production(self) -> bool:
        """True when running in the production profile (drives log rendering, etc.)."""
        return self.env == "production"
