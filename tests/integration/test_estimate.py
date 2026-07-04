"""Integration test: /v1/estimate — sane, close to actual, chat contract intact."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conduit.config import Settings
from conduit.main import create_app, lifespan
from conduit.providers.base import ModelPricing
from conduit.services.keys import KeyService
from conduit.services.usage import compute_cost

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# gpt-4o-mini catalog pricing (see providers/openai.py DEFAULT_OPENAI_MODELS).
PRICING = ModelPricing(input_per_1k_usd=0.00015, output_per_1k_usd=0.0006)

# Engineer the request so the coarse estimate matches the mocked usage exactly:
# prompt_text = "user: " + 44 chars = 50 chars // 4 = 12 tokens; max_tokens = 5.
CONTENT = "a" * 44
MAX_TOKENS = 5
OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
}


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def test_estimate_is_sane_close_to_actual_and_leaves_chat_intact(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key="upstream-credential-do-not-log-12345",
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        headers = {"Authorization": f"Bearer {key}"}
        body = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": CONTENT}],
            "max_tokens": MAX_TOKENS,
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            estimate = await client.post("/v1/estimate", headers=headers, json=body)
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                chat = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert estimate.status_code == 200
    est = estimate.json()
    chosen = est["chosen"]
    assert chosen["provider"] == "openai"
    assert chosen["model"] == "gpt-4o-mini"
    predicted = Decimal(chosen["estimated_cost_usd"])
    assert predicted > 0

    # Actual cost of the mocked completion.
    actual = compute_cost(PRICING, 12, 5)
    # Engineered to match; assert within a generous 25% tolerance regardless.
    assert abs(predicted - actual) <= actual * Decimal("0.25")

    # The standard chat contract is unchanged — a normal completion, no estimate field.
    assert chat.status_code == 200
    chat_body = chat.json()
    assert chat_body["object"] == "chat.completion"
    assert "estimated_cost_usd" not in chat_body
    assert chat_body["usage"]["total_tokens"] == 17
