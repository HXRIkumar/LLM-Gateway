"""End-to-end smart routing (respx, two providers, real Postgres/Redis).

An alias ``smart`` spans OpenAI (gpt-4o-mini) and Ollama (llama3.2). Policies pick
the strategy; capability filtering and the smart fallback plan are exercised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from conduit.config import Settings
from conduit.infra.db.models import UsageRecord
from conduit.main import create_app, lifespan
from conduit.services.keys import IssuedKey, KeyService
from conduit.services.policies import PolicyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"


def _openai_response(content: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _ollama_response(content: str) -> dict[str, object]:
    return {
        "model": "llama3.2",
        "created_at": "t",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 1,
        "eval_count": 1,
    }


@pytest.fixture(autouse=True)
async def _clean(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE usage_record, routing_policy CASCADE"))
    finally:
        await engine.dispose()
    yield


def _app(postgres_url: str, redis_url: str) -> FastAPI:
    return create_app(
        Settings(  # type: ignore[call-arg]
            database_url=postgres_url,
            redis_url=redis_url,
            openai_api_key="sk-test",
            rate_limit_enabled=False,
            retry_max_attempts=1,
            model_aliases={"smart": ["gpt-4o-mini", "llama3.2"]},
        )
    )


async def _mint(app: FastAPI) -> IssuedKey:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        return await service.issue(org.id)


def _body(model: str, *, vision: bool = False) -> dict[str, object]:
    content: object = "hi"
    if vision:
        content = [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:x"}},
        ]
    return {"model": model, "messages": [{"role": "user", "content": content}]}


async def test_cost_policy_routes_to_cheapest(postgres_url: str, redis_url: str) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        issued = await _mint(app)
        await PolicyService(app.state.db_sessionmaker).upsert(
            objective="cost", org_id=issued.org_id
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OLLAMA_URL).mock(
                    return_value=httpx.Response(200, json=_ollama_response("via ollama"))
                )
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {issued.plaintext}"},
                    json=_body("smart"),
                )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "via ollama"  # free = cheapest


async def test_latency_policy_routes_to_fastest(postgres_url: str, redis_url: str) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        issued = await _mint(app)
        await PolicyService(app.state.db_sessionmaker).upsert(
            objective="latency", org_id=issued.org_id
        )
        # Seed latency stats: OpenAI fast, Ollama slow.
        async with app.state.db_sessionmaker() as session:
            for provider, model, latency in (
                ("openai", "gpt-4o-mini", 50),
                ("ollama", "llama3.2", 900),
            ):
                session.add(
                    UsageRecord(
                        org_id=issued.org_id,
                        api_key_id=issued.id,
                        provider=provider,
                        model=model,
                        cost_usd=Decimal("0"),
                        latency_ms=latency,
                        status="ok",
                    )
                )
            await session.commit()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=_openai_response("via openai"))
                )
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {issued.plaintext}"},
                    json=_body("smart"),
                )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "via openai"  # fastest observed


async def test_capability_filter_excludes_incapable_provider(
    postgres_url: str, redis_url: str
) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        issued = await _mint(app)
        await PolicyService(app.state.db_sessionmaker).upsert(
            objective="cost", org_id=issued.org_id
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                # Vision request → Ollama (no vision) filtered out even though cheaper.
                respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=_openai_response("vision via openai"))
                )
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {issued.plaintext}"},
                    json=_body("smart", vision=True),
                )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "vision via openai"


async def test_smart_fallback_when_primary_fails(postgres_url: str, redis_url: str) -> None:
    app = _app(postgres_url, redis_url)
    async with lifespan(app):
        issued = await _mint(app)
        await PolicyService(app.state.db_sessionmaker).upsert(
            objective="cost", org_id=issued.org_id
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                # Cheapest (Ollama) fails → walk the smart plan to OpenAI.
                respx.post(OLLAMA_URL).mock(
                    return_value=httpx.Response(503, json={"error": {"message": "down"}})
                )
                respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=_openai_response("fallback via openai"))
                )
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {issued.plaintext}"},
                    json=_body("smart"),
                )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "fallback via openai"
