"""Central error rendering.

Every client-visible error is produced here as an OpenAI-shaped envelope, from a
single place, so the shape is consistent across the whole API. Domain exceptions
carry their own status/type/param/code; this module turns them into responses
and never leaks stack traces, secrets, or request bodies.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
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
    return error_response(
        status_code=exc.status_code,
        message=exc.message,
        error_type=exc.error_type,
        param=exc.param,
        code=exc.code,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the exception handlers onto the app."""
    app.add_exception_handler(ConduitError, conduit_error_handler)
