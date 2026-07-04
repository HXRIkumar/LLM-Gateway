"""Async session management.

The engine is shared; sessions are per-unit-of-work. The app factory builds one
``async_sessionmaker`` bound to the engine and stores it on ``app.state``; the
``get_db_session`` dependency (in ``api/deps.py``) yields a session per request.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to the shared engine."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
