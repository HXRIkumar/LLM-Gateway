"""Integration test: opt-in replay capture (off by default; redaction-safe)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from conduit.config import Settings
from conduit.infra.db.models import RequestLog
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
UPSTREAM_KEY = "upstream-credential-do-not-log-12345"  # stand-in secret (not sk-)
PROMPT = "the user's private question"
OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "an answer"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8},
}


@pytest.fixture(autouse=True)
async def _clean_request_log(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE request_log"))
    finally:
        await engine.dispose()
    yield


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def _post(app: FastAPI, key: str) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        with respx.mock:
            respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": PROMPT}]},
            )
    assert resp.status_code == 200


async def test_capture_enabled_stores_replayable_record(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key=UPSTREAM_KEY,
        replay_capture_enabled=True,
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        await _post(app, key)
        async with app.state.db_sessionmaker() as session:
            rows = (await session.scalars(select(RequestLog))).all()

    assert len(rows) == 1
    record = rows[0]
    assert record.provider == "openai"
    assert record.model == "gpt-4o-mini"
    # Replayable: the canonical request + response bodies are present.
    assert record.request["messages"][0]["content"] == PROMPT
    assert record.response["choices"][0]["message"]["content"] == "an answer"
    # Redaction-safe: no auth header / upstream credential / gateway key stored.
    blob = f"{record.request}{record.response}"
    assert UPSTREAM_KEY not in blob
    assert key not in blob
    assert "Authorization" not in blob


async def test_capture_disabled_by_default_stores_nothing(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, openai_api_key=UPSTREAM_KEY
    )
    assert settings.replay_capture_enabled is False
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        await _post(app, key)
        async with app.state.db_sessionmaker() as session:
            count = await session.scalar(select(func.count()).select_from(RequestLog))
    assert count == 0
