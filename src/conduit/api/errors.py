"""Central error rendering.

Every client-visible error is produced here as an OpenAI-shaped envelope, from a
single place, so the shape is consistent across the whole API. Domain exceptions
carry their own status/type/param/code; this module turns them into responses
and never leaks stack traces, secrets, or request bodies.
"""

from __future__ import annotations

import math

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from conduit.domain.errors import ConduitError
from conduit.domain.schemas import ErrorDetail, ErrorResponse

logger = structlog.get_logger("conduit.errors")


def error_response(
    *,
    status_code: int,
    message: str,
    error_type: str,
    param: str | None = None,
    code: str | None = None,
) -> JSONResponse:
    """Build an OpenAI-shaped error envelope response."""
    body = ErrorResponse(
        error=ErrorDetail(message=message, type=error_type, param=param, code=code)
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def conduit_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a domain :class:`ConduitError` as its OpenAI envelope."""
    if not isinstance(exc, ConduitError):  # pragma: no cover - registered only for ConduitError
        raise exc
    if exc.status_code >= 500:
        logger.error(
            "request failed",
            error_type=exc.error_type,
            status=exc.status_code,
            code=exc.code,
        )
    response = error_response(
        status_code=exc.status_code,
        message=exc.message,
        error_type=exc.error_type,
        param=exc.param,
        code=exc.code,
    )
    # Rate-limit errors advertise when to retry.
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, int | float):
        response.headers["Retry-After"] = str(max(1, math.ceil(retry_after)))
    return response


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a request-validation failure as an OpenAI-shaped 400."""
    param: str | None = None
    message = "invalid request"
    if isinstance(exc, RequestValidationError) and exc.errors():
        first = exc.errors()[0]
        location = [str(part) for part in first.get("loc", ()) if part != "body"]
        param = ".".join(location) or None
        message = str(first.get("msg", message))
        if param:
            message = f"{message} (at '{param}')"
    return error_response(
        status_code=400,
        message=message,
        error_type="invalid_request_error",
        param=param,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: a clean 500 envelope, details logged, never leaked."""
    logger.error("unhandled exception", exc_info=exc)
    return error_response(
        status_code=500,
        message="internal server error",
        error_type="api_error",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the exception handlers onto the app."""
    app.add_exception_handler(ConduitError, conduit_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
