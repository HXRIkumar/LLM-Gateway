"""Unit tests for the Settings object."""

from __future__ import annotations

from pydantic import SecretStr

from conduit.config import Settings


def test_defaults_target_local_dev() -> None:
    settings = Settings()
    assert settings.env == "development"
    assert settings.is_production is False
    assert settings.host == "0.0.0.0"
    assert settings.port == 8080
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.redis_url.startswith("redis://")
    assert isinstance(settings.admin_api_key, SecretStr)
    assert settings.openai_api_key is None


def test_env_prefix_overrides(monkeypatch) -> None:
    monkeypatch.setenv("CONDUIT_ENV", "production")
    monkeypatch.setenv("CONDUIT_PORT", "9999")
    monkeypatch.setenv("CONDUIT_OPENAI_API_KEY", "sk-test-123")
    settings = Settings()
    assert settings.env == "production"
    assert settings.is_production is True
    assert settings.port == 9999
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-123"


def test_secrets_are_masked_in_repr() -> None:
    settings = Settings()
    # SecretStr must never render its plaintext in a repr/str (log-safety).
    assert "change-me" not in repr(settings.admin_api_key)
    assert "change-me" not in str(settings.admin_api_key)
