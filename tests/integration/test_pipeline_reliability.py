"""End-to-end: one request exercises every reliability stage together.

preflight (rate limit + budget) → route → execute (retry → breaker → fallback) →
account — on a single request that retries the primary, trips over to the
fallback provider, and records usage for the provider that actually served it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

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
from conduit.services.budgets import BudgetService
from conduit.services.keys import IssuedKey, KeyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_RESPONSE = {
    "model": "llama3.2",
    "created_at": "t",
    "message": {"role": "assistant", "content": "served by ollama"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 5,
    "eval_count": 3,
}


@pytest.fixture(autouse=True)
async def _clean(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE usage_record, budget CASCADE"))
    finally:
        await engine.dispose()
    yield


async def _mint_key(app: FastAPI) -> IssuedKey:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        return await service.issue(org.id)


async def test_full_pipeline_retry_then_fallback_with_all_stages_live(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key="sk-test",
        retry_max_attempts=2,
        retry_base_delay_seconds=0.0,
        retry_max_delay_seconds=0.0,
        breaker_failure_threshold=10,  # high — won't open during this single request
        model_fallbacks={"gpt-4o-mini": ["llama3.2"]},
    )
    app = create_app(settings)
    async with lifespan(app):
        issued = await _mint_key(app)
        # A generous budget so the preflight budget check runs and admits.
        await BudgetService(app.state.db_sessionmaker).set_budget(
            limit_usd=Decimal("100"), period="daily", org_id=issued.org_id
        )
        headers = {"Authorization": f"Bearer {issued.plaintext}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                openai_route = respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(503, json={"error": {"message": "overloaded"}})
                )
                ollama_route = respx.post(OLLAMA_URL).mock(
                    return_value=httpx.Response(200, json=OLLAMA_RESPONSE)
                )
                resp = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                )

    # Rate limit + budget admitted; primary retried twice then fell back to Ollama.
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "served by ollama"
    assert openai_route.call_count == 2  # retry_max_attempts
    assert ollama_route.call_count == 1  # fallback served it

    # Accounting recorded one row, attributed to the provider that actually served.
    engine = create_async_engine(postgres_url)
    try:
        async with async_sessionmaker(engine)() as session:
            rows = list(await session.scalars(select(UsageRecord)))
    finally:
        await engine.dispose()
    assert len(rows) == 1
    assert rows[0].provider == "ollama"
    assert rows[0].total_tokens == 8
    assert rows[0].status == "ok"
