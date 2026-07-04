"""Integration tests for the streaming (SSE) chat path, both providers.

Asserts correct SSE framing (`data: <chunk>` events) and `data: [DONE]`
termination, and that the reconstructed content matches — for OpenAI's SSE and
Ollama's NDJSON, changing only the model.
"""

from __future__ import annotations

import json

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

OPENAI_SSE = (
    'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
    '"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
    'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
    '"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}\n\n'
    'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-4o-mini",'
    '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)

OLLAMA_NDJSON = (
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":"Hello"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":" world"},"done":false}\n'
    '{"model":"llama3.2","created_at":"t","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop"}\n'
)


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


def _reconstruct(sse_text: str) -> tuple[list[str], str]:
    """Return (data-payloads, concatenated delta content) from an SSE body."""
    payloads = [
        line[len("data: ") :] for line in sse_text.splitlines() if line.startswith("data: ")
    ]
    content = ""
    for payload in payloads:
        if payload == "[DONE]":
            continue
        obj = json.loads(payload)
        content += obj["choices"][0]["delta"].get("content") or ""
    return payloads, content


@pytest.mark.parametrize(
    ("model", "upstream_url", "upstream_response"),
    [
        ("gpt-4o-mini", OPENAI_URL, httpx.Response(200, text=OPENAI_SSE)),
        ("llama3.2", OLLAMA_URL, httpx.Response(200, text=OLLAMA_NDJSON)),
    ],
)
async def test_streaming_sse_framing_and_termination(
    postgres_url: str,
    redis_url: str,
    model: str,
    upstream_url: str,
    upstream_response: httpx.Response,
) -> None:
    settings = Settings(database_url=postgres_url, redis_url=redis_url, openai_api_key="sk-test")  # type: ignore[arg-type]
    app = create_app(settings)

    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(upstream_url).mock(return_value=upstream_response)
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                    },
                )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert body.endswith("data: [DONE]\n\n")

    payloads, content = _reconstruct(body)
    assert payloads[-1] == "[DONE]"
    assert content == "Hello world"
    # every non-terminal event is a valid chat.completion.chunk
    for payload in payloads[:-1]:
        assert json.loads(payload)["object"] == "chat.completion.chunk"
