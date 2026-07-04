"""Replay capture + lookup (opt-in, off by default).

Persists the canonical request + response so a recorded request can later be
re-run through the current pipeline (services/replay Task 5). Capture stores the
bodies (needed to replay) but never auth headers, key plaintext, or upstream
credentials — those never reach this layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conduit.domain.schemas import ChatCompletionRequest, ChatCompletionResponse
from conduit.infra.db.models import RequestLog


class ReplayService:
    """Store and retrieve replayable request/response records."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def capture(
        self,
        *,
        org_id: uuid.UUID,
        api_key_id: uuid.UUID,
        provider: str,
        model: str,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        status: str = "ok",
    ) -> uuid.UUID:
        record = RequestLog(
            org_id=org_id,
            api_key_id=api_key_id,
            provider=provider,
            model=model,
            request=request.model_dump(mode="json", exclude_none=True),
            response=response.model_dump(mode="json", exclude_none=True),
            status=status,
        )
        async with self._sessionmaker() as session:
            session.add(record)
            await session.commit()
            return record.id

    async def get(self, replay_id: uuid.UUID) -> RequestLog | None:
        async with self._sessionmaker() as session:
            record: RequestLog | None = await session.scalar(
                select(RequestLog).where(RequestLog.id == replay_id)
            )
            return record
