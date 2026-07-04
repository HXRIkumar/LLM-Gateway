"""Unit tests for the domain error hierarchy."""

from __future__ import annotations

import pytest

from conduit.domain.errors import (
    AllProvidersFailed,
    AuthError,
    ConduitError,
    ModelNotFound,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    UpstreamInvalidRequest,
    ValidationError,
)


def test_auth_error_maps_to_401() -> None:
    err = AuthError("no key")
    assert err.status_code == 401
    assert err.error_type == "authentication_error"
    assert err.code == "invalid_api_key"
    assert err.message == "no key"


def test_validation_error_maps_to_400() -> None:
    err = ValidationError("bad field", param="temperature")
    assert err.status_code == 400
    assert err.error_type == "invalid_request_error"
    assert err.param == "temperature"


def test_model_not_found_is_a_404_with_code() -> None:
    err = ModelNotFound("no such model")
    assert err.status_code == 404
    assert err.error_type == "invalid_request_error"
    assert err.code == "model_not_found"


def test_explicit_code_overrides_default() -> None:
    err = AuthError("revoked", code="key_revoked")
    assert err.code == "key_revoked"


@pytest.mark.parametrize(
    ("exc", "status", "error_type"),
    [
        (ProviderError("x"), 502, "api_error"),
        (ProviderTimeout("x"), 504, "api_error"),
        (ProviderRateLimited("x"), 429, "rate_limit_error"),
        (UpstreamInvalidRequest("x"), 400, "invalid_request_error"),
        (AllProvidersFailed("x"), 502, "api_error"),
    ],
)
def test_provider_errors_carry_expected_http_mapping(
    exc: ConduitError, status: int, error_type: str
) -> None:
    assert exc.status_code == status
    assert exc.error_type == error_type


def test_all_domain_errors_subclass_conduit_error() -> None:
    for exc_cls in (AuthError, ValidationError, ModelNotFound, ProviderError, AllProvidersFailed):
        assert issubclass(exc_cls, ConduitError)
