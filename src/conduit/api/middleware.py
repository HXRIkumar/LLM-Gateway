"""Edge middleware.

A pure-ASGI middleware (not ``BaseHTTPMiddleware``, which buffers and breaks SSE)
that assigns every request a correlation id, binds it into the structlog
contextvars so downstream logs carry it, echoes it back as ``X-Request-ID``, and
emits one structured access log per request with method, path, status, and
duration. It never touches the request/response body, so streaming is unaffected.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
