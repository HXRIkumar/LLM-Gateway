"""Persistence models — the durable system of record (Postgres).

Phase 1 modelled ``organization`` and ``api_key``. Phase 2 adds ``usage_record``
(the append-only usage/cost ledger, and the source of truth budgets read from)
and ``budget``. Routing-policy tables arrive with Phase 3. Gateway-issued keys
are stored only as a hash plus a lookup prefix; the plaintext never touches the DB.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from conduit.infra.db.base import Base


class ApiKeyStatus(StrEnum):
    """Lifecycle status of a gateway-issued API key."""

    ACTIVE = "active"
    REVOKED = "revoked"


class BudgetPeriod(StrEnum):
    """The spend window a budget resets on."""

    DAILY = "daily"
    MONTHLY = "monthly"


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


class Budget(Base):
    """A per-org spend cap for a recurring period (org-scoped in Phase 2)."""

    __tablename__ = "budget"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), unique=True, index=True
    )
    limit_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    period: Mapped[str] = mapped_column(String(16), default=BudgetPeriod.MONTHLY)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageRollup(Base):
    """Per-org daily aggregate of the usage ledger (produced by the rollup worker)."""

    __tablename__ = "usage_rollup"
    __table_args__ = (UniqueConstraint("org_id", "day", name="uq_usage_rollup_org_id_day"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    day: Mapped[date] = mapped_column(Date)
    request_count: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
