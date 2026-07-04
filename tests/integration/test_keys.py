"""Integration tests for key management and authentication (real Postgres)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conduit.config import Settings
from conduit.domain.errors import AuthError
from conduit.infra.db.models import ApiKey, ApiKeyStatus
from conduit.main import create_app, lifespan
from conduit.services.keys import KeyService, hash_token

pytestmark = pytest.mark.integration


async def test_issue_authenticate_revoke_rejected(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        async with sessionmaker() as session:
            service = KeyService(session)
            org = await service.get_or_create_default_org("acme")
            issued = await service.issue(org.id)
        plaintext = issued.plaintext

        # Persisted only as hash + prefix — never plaintext.
        async with sessionmaker() as session:
            row = await session.get(ApiKey, issued.id)
            assert row is not None
            assert row.key_hash == hash_token(plaintext)
            assert row.key_hash != plaintext
            assert row.prefix == plaintext[:12]
            assert row.status == ApiKeyStatus.ACTIVE

        # A valid key authenticates.
        async with sessionmaker() as session:
            principal = await KeyService(session).verify(plaintext)
            assert principal.api_key_id == issued.id
            assert principal.org_id == org.id

        # A bogus key is rejected.
        async with sessionmaker() as session:
            with pytest.raises(AuthError):
                await KeyService(session).verify("ck-not-a-real-key-value")

        # After revocation the same key no longer authenticates.
        async with sessionmaker() as session:
            await KeyService(session).revoke(issued.id)
        async with sessionmaker() as session:
            with pytest.raises(AuthError):
                await KeyService(session).verify(plaintext)
    finally:
        await engine.dispose()


async def test_admin_key_endpoints_and_guard(postgres_url: str) -> None:
    settings = Settings(database_url=postgres_url, admin_api_key="admin-secret")  # type: ignore[arg-type]
    app = create_app(settings)
    admin = {"Authorization": "Bearer admin-secret"}

    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # No / wrong admin credentials → OpenAI-shaped 401.
            resp = await client.post("/v1/admin/keys")
            assert resp.status_code == 401
            assert resp.json()["error"]["type"] == "authentication_error"

            resp = await client.post("/v1/admin/keys", headers={"Authorization": "Bearer wrong"})
            assert resp.status_code == 401

            # Create returns the plaintext exactly once.
            resp = await client.post("/v1/admin/keys", headers=admin)
            assert resp.status_code == 201
            created = resp.json()
            assert created["key"].startswith("ck-")
            key_id = created["id"]

            # List exposes prefix/status but no secrets.
            resp = await client.get("/v1/admin/keys", headers=admin)
            assert resp.status_code == 200
            listed = resp.json()
            assert any(item["id"] == key_id for item in listed)
            for item in listed:
                assert "key" not in item and "key_hash" not in item
                assert item["prefix"]

            # The freshly created key authenticates.
            engine = create_async_engine(postgres_url)
            try:
                async with async_sessionmaker(engine)() as session:
                    principal = await KeyService(session).verify(created["key"])
                    assert str(principal.api_key_id) == key_id
            finally:
                await engine.dispose()

            # Revoke, then it shows as revoked in the listing.
            resp = await client.delete(f"/v1/admin/keys/{key_id}", headers=admin)
            assert resp.status_code == 200
            assert resp.json()["status"] == "revoked"

            resp = await client.get("/v1/admin/keys", headers=admin)
            statuses = {item["id"]: item["status"] for item in resp.json()}
            assert statuses[key_id] == "revoked"
