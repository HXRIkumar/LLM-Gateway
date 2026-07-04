"""Integration test: /metrics exposes the key series and they move with traffic."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conduit.config import Settings
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
UPSTREAM_KEY = "upstream-credential-do-not-log-12345"  # stand-in secret (not sk-)
OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "reply body"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
}


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def _chat(client: AsyncClient, key: str) -> httpx.Response:
    return await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )


async def _scrape(client: AsyncClient) -> str:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    return resp.text


async def test_metrics_series_move_with_traffic(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key=UPSTREAM_KEY,
        # Keep retries minimal so the forced-error path is quick and deterministic.
        retry_max_attempts=1,
        breaker_enabled=False,
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # One success.
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                ok = await _chat(client, key)
            assert ok.status_code == 200

            # One forced upstream failure.
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(500, json={"e": "boom"}))
                bad = await _chat(client, key)
            assert bad.status_code >= 500

            body = await _scrape(client)

    # Success series present and incremented.
    assert 'conduit_requests_total{model="gpt-4o-mini",provider="openai",status="ok"} 1.0' in body
    assert (
        'conduit_requests_total{model="gpt-4o-mini",provider="openai",status="error"} 1.0' in body
    )
    # Tokens and cost recorded on the success.
    assert 'conduit_tokens_total{direction="prompt"} 11.0' in body
    assert 'conduit_tokens_total{direction="completion"} 5.0' in body
    assert 'conduit_cost_usd_total{model="gpt-4o-mini",provider="openai"}' in body
    # Upstream error recorded on the failure.
    assert 'conduit_upstream_errors_total{provider="openai"' in body

    # Cardinality guard: labels are bounded — never per-key or per-user.
    assert "api_key" not in body
    assert "key_prefix" not in body
    assert "principal" not in body
    assert key not in body


async def test_metrics_endpoint_404_when_disabled(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, metrics_enabled=False
    )
    app = create_app(settings)
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/metrics")
    assert resp.status_code == 404
