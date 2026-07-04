"""Persistence models — the durable system of record (Postgres).

Phase 1 modelled ``organization`` and ``api_key``. Phase 2 adds ``usage_record``
(the append-only usage/cost ledger, and the source of truth budgets read from)
and ``budget``. Routing-policy tables arrive with Phase 3. Gateway-issued keys
are stored only as a hash plus a lookup prefix; the plaintext never touches the DB.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
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


class UsageRecord(Base):
    """Append-only ledger: one row per completed request (tokens + cost + latency).

    The system of record for accounting; budgets sum ``cost_usd`` over a period.
    """

    __tablename__ = "usage_record"
    __table_args__ = (Index("ix_usage_record_org_id_created_at", "org_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    api_key_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_key.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
