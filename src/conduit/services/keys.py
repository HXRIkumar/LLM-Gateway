"""API key issuance, verification, listing, and revocation.

Gateway keys are shown in plaintext exactly once, at issue time. Only a SHA-256
hash and a short lookup ``prefix`` are persisted — the plaintext never touches
the database or the logs. Verification looks candidates up by prefix and does a
constant-time hash comparison (``hmac.compare_digest``).

Keys carry high entropy (256 random bits), so a fast cryptographic hash is the
right choice; a slow password KDF would add nothing here.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conduit.domain.errors import AuthError, NotFound
from conduit.infra.db.models import ApiKey, ApiKeyStatus, Organization
from conduit.services.orgs import DEFAULT_ORG_NAME, get_or_create_org

KEY_SCHEME = "ck-"
PREFIX_LENGTH = 12  # length of the stored lookup prefix (includes the scheme)


def generate_token() -> str:
    """Mint a new high-entropy gateway key."""
    return f"{KEY_SCHEME}{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a token — what gets persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    """The indexed lookup prefix derived from a token."""
    return token[:PREFIX_LENGTH]


@dataclass(frozen=True, slots=True)
class IssuedKey:
    """A freshly issued key; ``plaintext`` is exposed only here, only once."""

    id: uuid.UUID
    org_id: uuid.UUID
    prefix: str
    plaintext: str


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated identity resolved from a valid key."""

    api_key_id: uuid.UUID
    org_id: uuid.UUID
    prefix: str


class KeyService:
    """Manages the lifecycle of gateway API keys against Postgres."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_default_org(self, name: str = DEFAULT_ORG_NAME) -> Organization:
        return await get_or_create_org(self._session, name)

    async def issue(self, org_id: uuid.UUID) -> IssuedKey:
        """Create a key, persisting only its hash + prefix; return the plaintext once."""
        token = generate_token()
        prefix = token_prefix(token)
        api_key = ApiKey(
            org_id=org_id,
            key_hash=hash_token(token),
            prefix=prefix,
            status=ApiKeyStatus.ACTIVE,
        )
        self._session.add(api_key)
        await self._session.flush()
        issued = IssuedKey(id=api_key.id, org_id=org_id, prefix=prefix, plaintext=token)
        await self._session.commit()
        return issued

    async def verify(self, token: str) -> Principal:
        """Resolve a plaintext token to a :class:`Principal` or raise ``AuthError``."""
        if not token or not token.startswith(KEY_SCHEME):
            raise AuthError("invalid API key")
        digest = hash_token(token)
        candidates = await self._session.scalars(
            select(ApiKey).where(
                ApiKey.prefix == token_prefix(token),
                ApiKey.status == ApiKeyStatus.ACTIVE,
            )
        )
        for api_key in candidates:
            if hmac.compare_digest(api_key.key_hash, digest):
                return Principal(
                    api_key_id=api_key.id, org_id=api_key.org_id, prefix=api_key.prefix
                )
        raise AuthError("invalid API key")

    async def list_keys(self) -> list[ApiKey]:
        result = await self._session.scalars(select(ApiKey).order_by(ApiKey.created_at))
        return list(result)

    async def revoke(self, key_id: uuid.UUID) -> None:
        api_key = await self._session.get(ApiKey, key_id)
        if api_key is None:
            raise NotFound(f"api key {key_id} not found")
        api_key.status = ApiKeyStatus.REVOKED
        api_key.revoked_at = datetime.now(UTC)
        await self._session.commit()
