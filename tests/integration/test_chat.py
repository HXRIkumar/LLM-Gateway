"""Integration tests for the chat + models endpoints (real Postgres, mocked providers).

Proves the Phase 1 DoD for the unary path: a stock request succeeds against both
OpenAI and Ollama by changing only ``model``; bad keys → 401 and malformed bodies
→ 400, both OpenAI-shaped.
"""

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
OLLAMA_URL = "http://localhost:11434/api/chat"

OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hi from openai"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

OLLAMA_RESPONSE = {
    "model": "llama3.2",
    "created_at": "2024-01-01T00:00:00Z",
    "message": {"role": "assistant", "content": "Ollama says hi"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 3,
    "eval_count": 2,
}


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


def _body(model: str) -> dict[str, object]:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


async def test_unary_chat_across_providers_and_error_paths(postgres_url: str) -> None:
    settings = Settings(database_url=postgres_url, openai_api_key="sk-test")  # type: ignore[arg-type]
    app = create_app(settings)

    async with lifespan(app):
        key = await _mint_key(app)
        auth = {"Authorization": f"Bearer {key}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))

                openai_resp = await client.post(
                    "/v1/chat/completions", headers=auth, json=_body("gpt-4o-mini")
                )
                assert openai_resp.status_code == 200, openai_resp.text
                assert openai_resp.json()["choices"][0]["message"]["content"] == "hi from openai"

                # Change only the model → routed to Ollama, still canonical output.
                ollama_resp = await client.post(
                    "/v1/chat/completions", headers=auth, json=_body("llama3.2")
                )
                assert ollama_resp.status_code == 200, ollama_resp.text
                assert ollama_resp.json()["choices"][0]["message"]["content"] == "Ollama says hi"

            # Malformed body (missing model) → OpenAI-shaped 400.
            bad = await client.post(
                "/v1/chat/completions",
                headers=auth,
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
            assert bad.status_code == 400
            assert bad.json()["error"]["type"] == "invalid_request_error"
            assert bad.json()["error"]["param"] == "model"

            # Missing key → OpenAI-shaped 401.
            unauth = await client.post("/v1/chat/completions", json=_body("gpt-4o-mini"))
            assert unauth.status_code == 401
            assert unauth.json()["error"]["type"] == "authentication_error"

            # Unknown model → 404 model_not_found (routing fails before any provider call).
            unknown = await client.post(
                "/v1/chat/completions", headers=auth, json=_body("no-such-model")
            )
            assert unknown.status_code == 404
            assert unknown.json()["error"]["code"] == "model_not_found"


async def test_models_endpoint_lists_advertised_models(postgres_url: str) -> None:
    settings = Settings(database_url=postgres_url)
    app = create_app(settings)

    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["object"] == "list"
            ids = {item["id"] for item in data["data"]}
            assert "gpt-4o-mini" in ids
            assert "llama3.2" in ids

            # /v1/models requires auth, like OpenAI.
            assert (await client.get("/v1/models")).status_code == 401
