"""Domain models for InvoicePilot.

Money is always handled with Decimal and quantised to cents (ROUND_HALF_UP)
so invoice math is exact and testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(value: Decimal) -> str:
    return f"{value:,.2f}"


@dataclass
class TimelineEvent:
    kind: str  # created | order | status | reminder | paid | note
    message: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "at": self.at.isoformat()}


@dataclass
class Invoice:
    id: str
    number: str
    client_name: str
    description: str
    subtotal: Decimal
    tax_rate: Decimal = Decimal("0")  # percent, e.g. Decimal("8.25")
    currency: str = "USD"
    due_date: date = field(default_factory=date.today)
    status: str = "DRAFT"  # DRAFT | SENT | PAID  (OVERDUE is derived)
    paypal_order_id: str | None = None
    pay_link: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    paid_at: datetime | None = None
    timeline: list[TimelineEvent] = field(default_factory=list)
    reminders: list[dict] = field(default_factory=list)
    source_text: str = ""

    def __post_init__(self) -> None:
        # Domain invariants — an invoice that violates these can never be
        # created, no matter which entry point built it.
        if self.subtotal <= 0:
            raise ValueError("Invoice amount must be greater than zero")
        if not (Decimal("0") <= self.tax_rate <= Decimal("100")):
            raise ValueError("Tax rate must be between 0% and 100%")
        if not self.client_name.strip():
            raise ValueError("Client name must not be empty")
        if not self.description.strip():
            raise ValueError("Description must not be empty")

    @property
    def tax_amount(self) -> Decimal:
        return money(self.subtotal * self.tax_rate / Decimal("100"))

    @property
    def total(self) -> Decimal:
        return money(self.subtotal + self.tax_amount)

    def effective_status(self, today: date | None = None) -> str:
        if self.status == "PAID":
            return "PAID"
        today = today or date.today()
        if self.due_date < today and self.status in ("SENT", "DRAFT"):
            return "OVERDUE"
        return self.status

    def add_event(self, kind: str, message: str) -> None:
        self.timeline.append(TimelineEvent(kind=kind, message=message))

    def to_dict(self, today: date | None = None) -> dict:
        return {
            "id": self.id,
            "number": self.number,
            "client_name": self.client_name,
            "description": self.description,
            "subtotal": fmt(self.subtotal),
            "tax_rate": str(self.tax_rate),
            "tax_amount": fmt(self.tax_amount),
            "total": fmt(self.total),
            "currency": self.currency,
            "due_date": self.due_date.isoformat(),
            "status": self.effective_status(today),
            "raw_status": self.status,
            "paypal_order_id": self.paypal_order_id,
            "pay_link": self.pay_link,
            "created_at": self.created_at.isoformat(),
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "timeline": [e.to_dict() for e in self.timeline],
            "reminders": self.reminders,
            "source_text": self.source_text,
        }
