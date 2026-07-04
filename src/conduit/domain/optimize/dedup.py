"""Single-flight: collapse concurrent identical calls into one execution.

Pure coordination primitive (asyncio only — no I/O, no framework). The first
caller for a key runs the function; concurrent callers for the same key await the
*same* result. A failure propagates to every waiter and is **not** memoized — the
next call re-executes. This is per-process (per worker); cross-process repeats are
absorbed by the shared response cache once the first result lands (ADR-0008).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class SingleFlight:
    """Deduplicate concurrent calls sharing a key (Go-style singleflight)."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future[object]] = {}

    async def do[T](self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            # A leader is already running this key — share its result/exception.
            return await existing  # type: ignore[return-value]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self._inflight[key] = future
        try:
            result = await fn()
        except BaseException as exc:
            self._inflight.pop(key, None)
            if not future.done():
                future.set_exception(exc)
            raise
        self._inflight.pop(key, None)
        if not future.done():
            future.set_result(result)
        return result
