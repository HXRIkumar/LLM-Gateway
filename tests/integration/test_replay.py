"""Integration test: replay a captured request through the current pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from conduit.config import Settings
from conduit.infra.db.models import RequestLog
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService

pytestmark = pytest.mark.integration

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"
ADMIN = {"Authorization": "Bearer admin-secret"}

OPENAI_RESPONSE = {
    "id": "chatcmpl-openai",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "from-openai"},
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
async def _clean_request_log(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE request_log"))
    finally:
        await engine.dispose()
    yield


async def _mint_key(app: FastAPI) -> str:
    async with app.state.db_sessionmaker() as session:
        service = KeyService(session)
        org = await service.get_or_create_default_org()
        issued = await service.issue(org.id)
    return issued.plaintext


async def test_replay_reruns_and_policy_override_changes_provider(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url,
        redis_url=redis_url,
        openai_api_key="upstream-credential-do-not-log-12345",
        admin_api_key="admin-secret",
        replay_capture_enabled=True,
        model_aliases={"smart": ["gpt-4o-mini", "llama3.2"]},
    )
    app = create_app(settings)
    async with lifespan(app):
        key = await _mint_key(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with respx.mock:
                respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=OPENAI_RESPONSE))
                respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=OLLAMA_RESPONSE))
                # Original request against the alias — captured for replay.
                original = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]},
                )
                assert original.status_code == 200
                original_content = original.json()["choices"][0]["message"]["content"]
                original_provider = "openai" if "openai" in original_content else "ollama"

                async with app.state.db_sessionmaker() as session:
                    record = (await session.scalars(select(RequestLog))).one()
                assert record.model == "smart"

                # Replay under a policy that denies the provider the original used —
                # routing must fall to the other provider.
                replayed = await client.post(
                    f"/v1/admin/replays/{record.id}",
                    headers=ADMIN,
                    json={"deny_providers": [original_provider]},
                )
    assert replayed.status_code == 200
    replayed_content = replayed.json()["choices"][0]["message"]["content"]
    assert replayed_content != original_content  # different provider served the replay
    assert original_provider not in replayed_content


async def test_replay_missing_record_is_404(postgres_url: str, redis_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url=postgres_url, redis_url=redis_url, admin_api_key="admin-secret"
    )
    app = create_app(settings)
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/v1/admin/replays/00000000-0000-0000-0000-000000000000", headers=ADMIN
            )
    assert resp.status_code == 404
