"""Relational schema for InvoicePilot Pro (Week-1 persistence layer).

SQLAlchemy 2.0 models implementing the execution-pack schema. The same
models run on SQLite (dev/test dialect) and Postgres (production dialect):
``DATABASE_URL`` selects the engine, e.g.
``postgresql+psycopg://user:pass@host/invoicepilot`` in production.

Portability rules applied throughout:
- Money is ``Numeric`` — never float.
- Payloads are JSON (JSONB on Postgres via variant).
- Primary keys are strings (UUIDs generated app-side) so no dialect-specific
  sequence/identity behaviour leaks into the schema.
- Every tenant row carries ``owner_id`` and every query in the repository
  layer (``app/db_store.py``) is scoped by it.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    auth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auth_provider_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class BusinessProfile(Base):
    __tablename__ = "business_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    logo_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    default_tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    # Last invoice sequence issued for this owner (per-owner numbering).
    invoice_number_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class InvoiceRow(Base):
    """The ``invoices`` table. Named ``InvoiceRow`` to avoid clashing with the
    domain dataclass ``app.models.Invoice`` that the store layer maps to."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("owner_id", "ref"),
        UniqueConstraint("owner_id", "number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # The public/domain invoice id (e.g. "inv-0001") and human number
    # ("INV-1001"); both unique per owner, not globally.
    ref: Mapped[str] = mapped_column(String(32), index=True)
    number: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    due_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text, default="")
    source_text: Mapped[str] = mapped_column(Text, default="")
    pay_link: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id"), index=True
    )
    description: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)


class PayPalOrder(Base):
    __tablename__ = "paypal_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id"), index=True
    )
    paypal_order_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="manual")
    position: Mapped[int] = mapped_column(Integer, default=0)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class AgentEvent(Base):
    """Activity-feed / timeline events. The domain invoice timeline is stored
    here (``payload`` carries the human-readable message) so the dashboard
    feed and the invoice timeline are one source of truth."""

    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    invoice_id: Mapped[str | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("owner_id", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Snapshot of the original response (the created invoice dict) so a replay
    # returns exactly what the first request returned.
    response_snapshot: Mapped[dict | None] = mapped_column(
        JSON_TYPE, nullable=True
    )
    invoice_id: Mapped[str | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


# ---------------------------------------------------------------------------
# Engine / session helpers
# ---------------------------------------------------------------------------

DEFAULT_DATABASE_URL = "sqlite:///invoicepilot-dev.db"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None):
    url = url or database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_all(engine) -> None:
    Base.metadata.create_all(engine)
