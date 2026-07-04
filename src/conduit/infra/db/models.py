"""Persistence models — the durable system of record (Postgres).

Phase 1 covers only the tables the MVP needs: ``organization`` and ``api_key``.
Usage, budget, and routing-policy tables arrive with their phases (V2/V3) and
are deliberately not modelled here yet. Gateway-issued keys are stored only as a
hash plus a lookup prefix; the plaintext key never touches the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from conduit.infra.db.base import Base


class ApiKeyStatus(StrEnum):
    """Lifecycle status of a gateway-issued API key."""

    ACTIVE = "active"
    REVOKED = "revoked"


class Organization(Base):
    """A tenant that owns API keys (and, later, budgets and policies)."""

    __tablename__ = "organization"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class ApiKey(Base):
    """A gateway-issued bearer key, stored as ``key_hash`` + lookup ``prefix``."""

    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ApiKeyStatus.ACTIVE, server_default=ApiKeyStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="api_keys")
