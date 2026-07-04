"""Integration test: one complete, correlated, privacy-safe access log per request."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from conduit.config import Settings
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
UPSTREAM_KEY = (
    "upstream-credential-do-not-log-12345"  # a stand-in secret (not sk- to satisfy scanners)
)
PROMPT_BODY = "the user's confidential prompt text"
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

REQUIRED_FIELDS = {
    "key_prefix",
    "provider",
    "model",
    "reason",
    "attempts",
    "breaker_outcome",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_usd",
    "latency_ms",
    "status",
}


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def test_access_log_is_complete_and_leaks_nothing(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, openai_api_key=UPSTREAM_KEY
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                with capture_logs() as logs:
                    resp = await client.post(
                        "/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": PROMPT_BODY}],
                        },
                    )
    assert resp.status_code == 200

    completed = [entry for entry in logs if entry.get("event") == "request.completed"]
    assert len(completed) == 1
    entry = completed[0]
    assert REQUIRED_FIELDS.issubset(entry)
    assert entry["provider"] == "openai"
    assert entry["model"] == "gpt-4o-mini"
    assert entry["status"] == "ok"
    assert entry["total_tokens"] == 16

    # Nothing forbidden anywhere in the captured logs.
    blob = str(logs)
    assert UPSTREAM_KEY not in blob  # upstream credential
    assert key not in blob  # gateway key plaintext
    assert PROMPT_BODY not in blob  # request body
    assert "reply body" not in blob  # response body
    # The key is referenced only by its non-secret prefix.
    assert entry["key_prefix"] == key[:12]
