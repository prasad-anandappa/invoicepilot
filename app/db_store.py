"""Database-backed invoice store (Week-1 persistence layer).

Implements the exact surface of the in-memory ``InvoiceStore``
(``next_number`` / ``add`` / ``by_idempotency_key`` /
``remember_idempotency_key`` / ``get`` / ``by_order`` / ``all``) on top of
the relational schema in ``app/db.py``, with one structural difference:
**every query is scoped by ``owner_id``**. A store instance is bound to one
owner; it can never read or write another owner's rows.

Mapping notes (domain ``Invoice`` dataclass <-> normalised tables):

- The client is a find-or-create ``clients`` row keyed by (owner_id, name).
- The domain model carries one aggregate amount, so an invoice persists as a
  single ``invoice_items`` row (quantity 1, unit price = subtotal). Real
  multi-line editing arrives with the Flutter create form (Week 3); the
  schema already supports it.
- The invoice timeline lives in ``agent_events`` (payload carries the
  message) — one source of truth for timeline and dashboard feed.
- ``reminders`` and the PayPal order are child rows replaced wholesale on
  each ``add``/``save`` (the store persists the full aggregate).

Persistence contract: unlike the in-memory store (which holds object
references, so in-place mutations are implicitly "saved"), this store
persists on ``add()`` / ``save()`` calls. Callers mutate the domain object,
then call ``save(invoice)`` to write the new state. Week-2 wires those save
points into the agent/API flows; Week-1 ships the layer plus its tests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from .db import (
    AgentEvent,
    BusinessProfile,
    Client,
    IdempotencyKey,
    InvoiceItem,
    InvoiceRow,
    PayPalOrder,
    Reminder,
    User,
    create_all,
    make_engine,
    make_session_factory,
)
from .models import Invoice, TimelineEvent

DEV_OWNER_ID = "dev-owner"


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on read; stored values are always UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


class DbInvoiceStore:
    def __init__(
        self,
        owner_id: str = DEV_OWNER_ID,
        session_factory=None,
        database_url: str | None = None,
    ) -> None:
        if session_factory is None:
            engine = make_engine(database_url)
            create_all(engine)
            session_factory = make_session_factory(engine)
        self._sessions = session_factory
        self.owner_id = owner_id
        self._ensure_owner()

    # -- owner bootstrap ----------------------------------------------------
    def _ensure_owner(self) -> None:
        """Create the owner user + default business profile if absent.

        Week-1 stand-in for real sign-up; Week-2 auth creates these rows and
        passes the authenticated user's id as ``owner_id``.
        """
        with self._sessions() as s:
            if s.get(User, self.owner_id) is None:
                s.add(
                    User(
                        id=self.owner_id,
                        email=f"{self.owner_id}@invoicepilot.local",
                        auth_provider="dev",
                    )
                )
                s.flush()
            profile = s.scalar(
                select(BusinessProfile).where(
                    BusinessProfile.owner_id == self.owner_id
                )
            )
            if profile is None:
                s.add(BusinessProfile(owner_id=self.owner_id))
            s.commit()

    # -- numbering ------------------------------------------------------------
    def next_number(self) -> tuple[str, str]:
        with self._sessions() as s:
            profile = s.scalar(
                select(BusinessProfile).where(
                    BusinessProfile.owner_id == self.owner_id
                )
            )
            profile.invoice_number_seq += 1
            n = profile.invoice_number_seq
            s.commit()
        return f"inv-{n:04d}", f"INV-{1000 + n}"

    # -- writes ---------------------------------------------------------------
    def add(self, invoice: Invoice) -> Invoice:
        """Insert or update the full invoice aggregate."""
        with self._sessions() as s:
            client = self._find_or_create_client(s, invoice.client_name)
            row = s.scalar(
                select(InvoiceRow).where(
                    InvoiceRow.owner_id == self.owner_id,
                    InvoiceRow.ref == invoice.id,
                )
            )
            if row is None:
                row = InvoiceRow(
                    owner_id=self.owner_id, ref=invoice.id, client_id=client.id
                )
                s.add(row)
            row.number = invoice.number
            row.client_id = client.id
            row.status = invoice.status
            row.currency = invoice.currency
            row.subtotal = invoice.subtotal
            row.tax_rate = invoice.tax_rate
            row.tax_total = invoice.tax_amount
            row.total = invoice.total
            row.due_date = invoice.due_date
            row.description = invoice.description
            row.source_text = invoice.source_text
            row.pay_link = invoice.pay_link
            row.paid_at = invoice.paid_at
            row.created_at = invoice.created_at
            s.flush()

            # Single aggregate line item (see module docstring).
            s.query(InvoiceItem).filter(
                InvoiceItem.invoice_id == row.id
            ).delete()
            s.add(
                InvoiceItem(
                    invoice_id=row.id,
                    description=invoice.description,
                    quantity=Decimal("1"),
                    unit_price=invoice.subtotal,
                    tax_rate=invoice.tax_rate,
                    line_total=invoice.subtotal,
                    position=0,
                )
            )

            # Timeline -> agent_events (replace wholesale, order preserved by
            # autoincrement id within the insert sequence).
            s.query(AgentEvent).filter(
                AgentEvent.invoice_id == row.id,
                AgentEvent.owner_id == self.owner_id,
            ).delete()
            for event in invoice.timeline:
                s.add(
                    AgentEvent(
                        owner_id=self.owner_id,
                        invoice_id=row.id,
                        kind=event.kind,
                        payload={"message": event.message},
                        created_at=event.at,
                    )
                )

            # Reminders (replace wholesale).
            s.query(Reminder).filter(
                Reminder.invoice_id == row.id,
                Reminder.owner_id == self.owner_id,
            ).delete()
            for position, record in enumerate(invoice.reminders):
                s.add(
                    Reminder(
                        owner_id=self.owner_id,
                        invoice_id=row.id,
                        kind="manual",
                        position=position,
                        channel=record.get("channel"),
                        message=record.get("message"),
                        sent_at=_parse_dt(record.get("at")),
                    )
                )

            # PayPal order (upsert by paypal_order_id).
            if invoice.paypal_order_id:
                order = s.scalar(
                    select(PayPalOrder).where(
                        PayPalOrder.paypal_order_id == invoice.paypal_order_id
                    )
                )
                if order is None:
                    order = PayPalOrder(
                        owner_id=self.owner_id,
                        invoice_id=row.id,
                        paypal_order_id=invoice.paypal_order_id,
                    )
                    s.add(order)
                order.invoice_id = row.id
                order.owner_id = self.owner_id
                order.status = invoice.status
                order.amount = invoice.total
                order.currency = invoice.currency
            s.commit()
        return invoice

    def save(self, invoice: Invoice) -> Invoice:
        """Explicit persist point for mutations (see module docstring)."""
        return self.add(invoice)

    # -- idempotency ------------------------------------------------------------
    def by_idempotency_key(self, key: str) -> Invoice | None:
        with self._sessions() as s:
            record = s.scalar(
                select(IdempotencyKey).where(
                    IdempotencyKey.owner_id == self.owner_id,
                    IdempotencyKey.key == key,
                )
            )
            if record is None or record.invoice_id is None:
                return None
            row = s.get(InvoiceRow, record.invoice_id)
            if row is None or row.owner_id != self.owner_id:
                return None
            return self._to_domain(s, row)

    def remember_idempotency_key(
        self, key: str, invoice: Invoice, request_hash: str | None = None
    ) -> None:
        with self._sessions() as s:
            row = s.scalar(
                select(InvoiceRow).where(
                    InvoiceRow.owner_id == self.owner_id,
                    InvoiceRow.ref == invoice.id,
                )
            )
            existing = s.scalar(
                select(IdempotencyKey).where(
                    IdempotencyKey.owner_id == self.owner_id,
                    IdempotencyKey.key == key,
                )
            )
            if existing is None:
                s.add(
                    IdempotencyKey(
                        owner_id=self.owner_id,
                        key=key,
                        request_hash=request_hash,
                        response_snapshot=invoice.to_dict(),
                        invoice_id=row.id if row else None,
                    )
                )
            s.commit()

    # -- reads ---------------------------------------------------------------
    def get(self, invoice_id: str) -> Invoice:
        with self._sessions() as s:
            row = s.scalar(
                select(InvoiceRow).where(
                    InvoiceRow.owner_id == self.owner_id,
                    InvoiceRow.ref == invoice_id,
                )
            )
            if row is None:
                raise KeyError(invoice_id)
            return self._to_domain(s, row)

    def by_order(self, order_id: str) -> Invoice | None:
        with self._sessions() as s:
            order = s.scalar(
                select(PayPalOrder).where(
                    PayPalOrder.owner_id == self.owner_id,
                    PayPalOrder.paypal_order_id == order_id,
                )
            )
            if order is None:
                return None
            row = s.get(InvoiceRow, order.invoice_id)
            if row is None or row.owner_id != self.owner_id:
                return None
            return self._to_domain(s, row)

    def all(self) -> list[Invoice]:
        with self._sessions() as s:
            rows = s.scalars(
                select(InvoiceRow)
                .where(InvoiceRow.owner_id == self.owner_id)
                .order_by(InvoiceRow.created_at, InvoiceRow.ref)
            ).all()
            return [self._to_domain(s, row) for row in rows]

    # -- mapping ---------------------------------------------------------------
    def _find_or_create_client(self, s, name: str) -> Client:
        client = s.scalar(
            select(Client).where(
                Client.owner_id == self.owner_id, Client.name == name
            )
        )
        if client is None:
            client = Client(owner_id=self.owner_id, name=name)
            s.add(client)
            s.flush()
        return client

    def _to_domain(self, s, row: InvoiceRow) -> Invoice:
        client = s.get(Client, row.client_id)
        events = s.scalars(
            select(AgentEvent)
            .where(
                AgentEvent.owner_id == self.owner_id,
                AgentEvent.invoice_id == row.id,
            )
            .order_by(AgentEvent.id)
        ).all()
        timeline = [
            TimelineEvent(
                kind=e.kind,
                message=(e.payload or {}).get("message", ""),
                at=_aware(e.created_at),
            )
            for e in events
        ]
        reminder_rows = s.scalars(
            select(Reminder)
            .where(
                Reminder.owner_id == self.owner_id,
                Reminder.invoice_id == row.id,
            )
            .order_by(Reminder.position)
        ).all()
        reminders = [
            {
                "at": _aware(r.sent_at).isoformat() if r.sent_at else None,
                "message": r.message,
                "channel": r.channel,
            }
            for r in reminder_rows
        ]
        order = s.scalar(
            select(PayPalOrder)
            .where(
                PayPalOrder.owner_id == self.owner_id,
                PayPalOrder.invoice_id == row.id,
            )
            .order_by(PayPalOrder.created_at.desc())
        )
        return Invoice(
            id=row.ref,
            number=row.number,
            client_name=client.name if client else "",
            description=row.description,
            subtotal=Decimal(row.subtotal),
            tax_rate=Decimal(row.tax_rate),
            currency=row.currency,
            due_date=row.due_date,
            status=row.status,
            paypal_order_id=order.paypal_order_id if order else None,
            pay_link=row.pay_link,
            created_at=_aware(row.created_at),
            paid_at=_aware(row.paid_at),
            timeline=timeline,
            reminders=reminders,
            source_text=row.source_text or "",
        )
