"""Structured logging setup.

structlog renders pretty, human-readable lines in development and JSON in
production. A single :func:`configure_logging` call wires the processor chain;
the request-id emitted by the edge middleware is merged in via contextvars so
every log line on the request path is correlated.
"""

from __future__ import annotations

import logging
import sys

import structlog

from conduit.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog for the process. Idempotent; safe to call per app."""
    level = logging.getLevelName(settings.log_level.upper())
    if not isinstance(level, int):
        level = logging.INFO

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        # Not cached: keeps loggers reconfigurable and capturable in tests; the
        # per-call processor lookup is negligible next to provider round-trips.
        cache_logger_on_first_use=False,
    )
