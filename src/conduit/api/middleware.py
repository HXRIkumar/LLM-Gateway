"""Edge middleware.

A pure-ASGI middleware (not ``BaseHTTPMiddleware``, which buffers and breaks SSE)
that assigns every request a correlation id, binds it into the structlog
contextvars so downstream logs carry it, echoes it back as ``X-Request-ID``, and
emits one structured access log per request with method, path, status, and
duration. It never touches the request/response body, so streaming is unaffected.
"""

from __future__ import annotations

import hmac
import time
import uuid
from typing import Annotated

import structlog
from fastapi import Depends, Header
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from conduit.api.deps import DbSessionDep, SettingsDep
from conduit.domain.errors import AuthError
from conduit.services.keys import KeyService, Principal

logger = structlog.get_logger("conduit.access")

REQUEST_ID_HEADER = "x-request-id"


class RequestContextMiddleware:
    """Assign a request id, bind logging context, and log access lines."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        method: str = scope["method"]
        path: str = scope["path"]
        start = time.perf_counter()
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                mutable = MutableHeaders(scope=message)
                mutable["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request",
                method=method,
                path=path,
                status=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()


# --- Bearer authentication ------------------------------------------------------


def _extract_bearer(authorization: str | None) -> str:
    """Pull the token out of an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        raise AuthError("missing bearer credentials")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise AuthError("malformed Authorization header; expected 'Bearer <key>'")
    return token


async def authenticate(
    session: DbSessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Resolve the request's bearer key to a principal, or raise a 401."""
    token = _extract_bearer(authorization)
    return await KeyService(session).verify(token)


def require_admin(
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Guard admin endpoints with the bootstrap admin key (constant-time compare)."""
    token = _extract_bearer(authorization)
    if not hmac.compare_digest(token, settings.admin_api_key.get_secret_value()):
        raise AuthError("invalid admin credentials")


CurrentPrincipal = Annotated[Principal, Depends(authenticate)]
AdminGuard = Annotated[None, Depends(require_admin)]
