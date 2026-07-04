"""Phase 5 end-to-end: predict + cache + dedup + semantic + adaptive on one path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from conduit.config import Settings
from conduit.infra.cache import RedisResponseCache
from conduit.infra.db.models import RequestLog
from conduit.infra.vector import RedisVectorIndex
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService
from conduit.services.semantic import SemanticCache

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"
ADMIN = {"Authorization": "Bearer admin-secret"}
ALIASES = {"smart": ["gpt-4o-mini", "llama3.2"]}

_VECTORS = {"MARKER_A": [1.0, 0.1, 0.0], "MARKER_B": [0.98, 0.2, 0.0]}


class _FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        for marker, vector in _VECTORS.items():
            if marker in text:
                return vector
        return [0.0, 0.0, 1.0]


def _openai_response(content: str) -> dict:
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
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


OLLAMA_RESPONSE = {
    "model": "llama3.2",
    "message": {"role": "assistant", "content": "from-ollama"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 5,
    "eval_count": 2,
}


@pytest.fixture(autouse=True)
async def _clean(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        # Truncate the ledger too: adaptive routing reads latency from usage_record,
        # so stale rows from other tests must not tip the balanced score.
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE request_log, usage_record"))
    finally:
        await engine.dispose()
    yield


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


def _cacheable(content: str) -> dict:
    return {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
    }


async def test_all_optimizations_coexist_on_one_request_path(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key="upstream-credential-do-not-log-12345",
        admin_api_key="admin-secret",
        semantic_cache_enabled=True,
        replay_capture_enabled=True,
        model_aliases=ALIASES,
    )
    app = create_app(settings)
    async with lifespan(app):
        app.state.semantic_cache = SemanticCache(
            _FakeEmbedder(),
            RedisVectorIndex(app.state.redis, max_entries=100, ttl_seconds=3600),
            RedisResponseCache(app.state.redis, 3600),
            settings.semantic_cache_threshold,
        )
        key = await _mint_key(app)
        headers = {"Authorization": f"Bearer {key}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            openai_calls = 0

            async def slow_openai(request: httpx.Request) -> httpx.Response:
                nonlocal openai_calls
                openai_calls += 1
                await asyncio.sleep(0.15)  # hold leader so dedup can collapse
                return httpx.Response(200, json=_openai_response("from-openai"))

            with respx.mock:
                respx.post(OPENAI_URL).mock(side_effect=slow_openai)
                respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))

                # 1) PREDICT — additive estimate endpoint, no upstream call.
                est = await client.post("/v1/estimate", headers=headers, json=_cacheable("hi"))
                assert est.status_code == 200
                from decimal import Decimal

                assert Decimal(est.json()["chosen"]["estimated_cost_usd"]) > 0

                # 2) DEDUP — 5 concurrent identical cold cacheable requests → 1 call.
                batch = await asyncio.gather(
                    *(
                        client.post(
                            "/v1/chat/completions",
                            headers=headers,
                            json=_cacheable("about MARKER_A"),
                        )
                        for _ in range(5)
                    )
                )
                assert all(r.status_code == 200 for r in batch)
                assert openai_calls == 1

                # 3) EXACT CACHE — a repeat is served from cache, still 1 call.
                again = await client.post(
                    "/v1/chat/completions", headers=headers, json=_cacheable("about MARKER_A")
                )
                assert again.json()["choices"][0]["message"]["content"] == "from-openai"
                assert openai_calls == 1

                # 4) SEMANTIC — a near paraphrase hits the cache, still 1 call.
                para = await client.post(
                    "/v1/chat/completions", headers=headers, json=_cacheable("re MARKER_B please")
                )
                assert para.json()["choices"][0]["message"]["content"] == "from-openai"
                assert openai_calls == 1

                # 5) ADAPTIVE — degrade ollama → the alias routes to openai.
                await app.state.redis.set("routestats:ollama:llama3.2", "0.9")
                smart = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]},
                )
                assert smart.json()["choices"][0]["message"]["content"] == "from-openai"
                assert openai_calls == 2  # only the cold batch + the adaptive route hit upstream

            # 6) REPLAY — a captured request re-runs through the pipeline.
            async with app.state.db_sessionmaker() as session:
                record = (await session.scalars(select(RequestLog))).first()
            assert record is not None
            with respx.mock:
                respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=_openai_response("replayed"))
                )
                respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))
                replayed = await client.post(f"/v1/admin/replays/{record.id}", headers=ADMIN)
            assert replayed.status_code == 200
