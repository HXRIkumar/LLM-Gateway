"""Integration tests for usage & cost accounting (real Postgres, mocked providers)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conduit.config import Settings
from conduit.infra.db.models import UsageRecord
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _clean_usage(postgres_url: str) -> AsyncIterator[None]:
    # The Postgres container is session-scoped and every chat request now writes
    # a usage row, so clear the ledger before each assertion here.
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE usage_record"))
    finally:
        await engine.dispose()
    yield


OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"

OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
}

OLLAMA_STREAM = (
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":"Hello"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"content":" world"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"content":""},"done":true,'
    '"done_reason":"stop","prompt_eval_count":40,"eval_count":10}\n'
)


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def _usage_rows(postgres_url: str) -> list[UsageRecord]:
    engine = create_async_engine(postgres_url)
    try:
        async with async_sessionmaker(engine)() as session:
            result = await session.scalars(select(UsageRecord).order_by(UsageRecord.created_at))
            return list(result)
    finally:
        await engine.dispose()


async def test_unary_writes_accurate_usage_row(postgres_url: str, redis_url: str) -> None:
    settings = Settings(database_url=postgres_url, redis_url=redis_url, openai_api_key="sk-test")  # type: ignore[arg-type]
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                )
        assert resp.status_code == 200

    rows = await _usage_rows(postgres_url)
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "openai"
    assert row.model == "gpt-4o-mini"
    assert row.prompt_tokens == 1000
    assert row.completion_tokens == 500
    assert row.total_tokens == 1500
    # 1000/1000*0.00015 + 500/1000*0.0006 = 0.00045
    assert float(row.cost_usd) == pytest.approx(0.00045)
    assert row.status == "ok"
    assert row.latency_ms >= 0


async def test_streaming_records_usage_at_stream_end(postgres_url: str, redis_url: str) -> None:
    settings = Settings(database_url=postgres_url, redis_url=redis_url)
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, text=OLLAMA_STREAM))
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": "llama3.2",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                    },
                )
        assert resp.status_code == 200
        assert resp.text.endswith("data: [DONE]\n\n")  # SSE framing intact

    rows = await _usage_rows(postgres_url)
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "ollama"
    assert row.model == "llama3.2"
    assert row.prompt_tokens == 40
    assert row.completion_tokens == 10
    assert row.total_tokens == 50
    assert float(row.cost_usd) == 0.0  # local models are free
    assert row.status == "ok"
