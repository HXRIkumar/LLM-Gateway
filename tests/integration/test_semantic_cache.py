"""Integration test: semantic (near-match) cache with a mocked embedder."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conduit.config import Settings
from conduit.infra.cache import RedisResponseCache
from conduit.infra.vector import RedisVectorIndex
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService
from conduit.services.semantic import SemanticCache

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
UPSTREAM_KEY = "upstream-credential-do-not-log-12345"  # stand-in secret (not sk-)
OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "Paris"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9},
}

# Marker → vector. A and B are near (cosine ~0.99); C is orthogonal.
_VECTORS = {
    "MARKER_A": [1.0, 0.1, 0.0],
    "MARKER_B": [0.98, 0.2, 0.0],
    "MARKER_C": [0.0, 0.0, 1.0],
}


class _FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        for marker, vector in _VECTORS.items():
            if marker in text:
                return vector
        return [0.0, 1.0, 0.0]


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


def _body(text: str, *, tools: bool = False) -> dict:
    body: dict = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": text}],
        "temperature": 0,
    }
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": "f"}}]
    return body


async def test_semantic_cache_hits_paraphrase_and_respects_guards(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key=UPSTREAM_KEY,
        semantic_cache_enabled=True,
    )
    app = create_app(settings)
    async with lifespan(app):
        # Swap the real embedder for a deterministic fake (real Redis index + cache).
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
            with respx.mock:
                route = respx.post(OPENAI_URL).mock(
                    return_value=httpx.Response(200, json=OPENAI_RESPONSE)
                )
                # A: cold miss → upstream + index the prompt vector.
                a = await client.post(
                    "/v1/chat/completions", headers=headers, json=_body("about MARKER_A")
                )
                # B: near paraphrase → semantic hit, no upstream call.
                b = await client.post(
                    "/v1/chat/completions", headers=headers, json=_body("re MARKER_B please")
                )
                calls_after_ab = route.call_count
                # C: orthogonal → below threshold → miss → upstream call.
                await client.post(
                    "/v1/chat/completions", headers=headers, json=_body("what about MARKER_C")
                )
                calls_after_c = route.call_count
                # Guarded (tools) → never cacheable/semantic → always upstream.
                await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json=_body("about MARKER_A", tools=True),
                )
                calls_after_guarded = route.call_count

        semantic_hits = app.state.metrics.registry.get_sample_value(
            "conduit_cache_events_total", {"event": "semantic_hit"}
        )

    assert a.status_code == b.status_code == 200
    assert b.json() == a.json()  # B served A's cached response
    assert calls_after_ab == 1  # B did NOT call upstream
    assert calls_after_c == 2  # C missed → called upstream
    assert calls_after_guarded == 3  # guarded request always calls upstream
    assert semantic_hits == 1.0
