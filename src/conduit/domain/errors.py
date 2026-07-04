"""Domain exception hierarchy.

Every client-visible failure is raised as a typed :class:`ConduitError`. The
edge (``api/errors.py``) is the single place that turns these into OpenAI-shaped
HTTP responses, reading ``status_code`` / ``error_type`` / ``code`` off the
exception. The domain stays pure — it knows the OpenAI *vocabulary* (error type
strings, status codes) but does no HTTP itself.
"""

from __future__ import annotations


class ConduitError(Exception):
    """Base for all domain errors mapped to an OpenAI-shaped envelope."""

    status_code: int = 500
    error_type: str = "api_error"
    default_code: str | None = None

    def __init__(self, message: str, *, param: str | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.param = param
        self.code = code if code is not None else self.default_code


# --- Client-facing (the caller's request is at fault) ---------------------------


class AuthError(ConduitError):
    """Missing, malformed, or revoked gateway API key."""

    status_code = 401
    error_type = "authentication_error"
    default_code = "invalid_api_key"


class ValidationError(ConduitError):
    """The request failed validation (bad shape, types, or values)."""

    status_code = 400
    error_type = "invalid_request_error"


class NotFound(ConduitError):
    """A referenced resource does not exist."""

    status_code = 404
    error_type = "invalid_request_error"


class RateLimited(ConduitError):
    """The caller exceeded a gateway rate limit. Carries a retry-after hint."""

    status_code = 429
    error_type = "rate_limit_error"
    default_code = "rate_limit_exceeded"

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        param: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, param=param, code=code)
        self.retry_after = retry_after


class BudgetExceeded(ConduitError):
    """The org has exhausted its spend budget for the current period."""

    status_code = 429
    error_type = "insufficient_quota"
    default_code = "insufficient_quota"


class ModelNotFound(NotFound):
    """The requested model maps to no configured provider."""

    default_code = "model_not_found"


# --- Provider-facing (an upstream provider is at fault) -------------------------


class ProviderError(ConduitError):
    """An upstream provider failed in a way the gateway could not satisfy."""

    status_code = 502
    error_type = "api_error"


class ProviderTimeout(ProviderError):
    """The upstream provider did not respond within the deadline."""

    status_code = 504
    default_code = "provider_timeout"


class ProviderRateLimited(ProviderError):
    """The upstream provider rate-limited the gateway."""

    status_code = 429
    error_type = "rate_limit_error"
    default_code = "provider_rate_limited"


class ProviderAuthError(ProviderError):
    """The gateway's upstream credentials were rejected — an operator problem."""

    status_code = 502
    default_code = "provider_authentication_error"


class UpstreamInvalidRequest(ProviderError):
    """The provider rejected the (translated) request as invalid."""

    status_code = 400
    error_type = "invalid_request_error"


class AllProvidersFailed(ConduitError):
    """Every provider in the routing decision's fallback plan failed."""

    status_code = 502
    error_type = "api_error"
    default_code = "all_providers_failed"
