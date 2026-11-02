"""InvoicePilot agent: the autonomous get-paid loop.

One agent, five tools:
  create_invoice   — NL note -> structured invoice (LLM w/ rule-based fallback)
  create_paypal_order — PayPal (sandbox/mock) order + buyer payment link
  check_status     — poll PayPal; capture approved orders; mark invoices paid
  send_reminder    — polite, context-aware overdue reminder
  reconcile        — money-in / money-out summary across all invoices

Every tool call returns human-readable "steps" so the UI can show the
agent working, step by step.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from .config import Settings
from .llm import parse_with_llm
from .models import Invoice, fmt, money
from .parser import parse_invoice_text
from .paypal_client import MockPayPalClient, PayPalClientError
from .store import InvoiceStore

DEFAULT_DUE_DAYS = 7


def _step(tool: str, summary: str, detail: str = "") -> dict:
    return {"tool": tool, "summary": summary, "detail": detail}


class InvoicePilotAgent:
    def __init__(self, settings: Settings, store: InvoiceStore, paypal) -> None:
        self.settings = settings
        self.store = store
        self.paypal = paypal

    # ------------------------------------------------------------------
    # Tool 1: create_invoice
    # ------------------------------------------------------------------
    def create_invoice(
        self, text: str, today: date | None = None
    ) -> tuple[Invoice, list[dict]]:
        today = today or date.today()
        steps: list[dict] = []
        parsed = parse_with_llm(text, self.settings)
        engine = "llm"
        if parsed is None:
            parsed = parse_invoice_text(text, today=today)
            engine = "rule-based parser"
        steps.append(
            _step(
                "create_invoice",
                f"Understood the job via {engine}",
                f"{parsed.description} for {parsed.client_name}",
            )
        )
        due = parsed.due_date or (today + timedelta(days=DEFAULT_DUE_DAYS))
        inv_id, number = self.store.next_number()
        invoice = Invoice(
            id=inv_id,
            number=number,
            client_name=parsed.client_name,
            description=parsed.description,
            subtotal=money(parsed.amount),
            tax_rate=parsed.tax_rate,
            due_date=due,
            source_text=text,
        )
        invoice.add_event(
            "created",
            f"Invoice {number} created — {invoice.description} for "
            f"{invoice.client_name}: ${fmt(invoice.total)} due {due.isoformat()}",
        )
        self.store.add(invoice)
        steps.append(
            _step(
                "create_invoice",
                f"Invoice {number} drafted",
                f"Subtotal ${fmt(invoice.subtotal)} + tax "
                f"${fmt(invoice.tax_amount)} = ${fmt(invoice.total)}",
            )
        )
        return invoice, steps

    def create_invoice_structured(
        self,
        *,
        description: str,
        client_name: str,
        amount: Decimal,
        tax_rate: Decimal = Decimal("0"),
        due_date: date | None = None,
        today: date | None = None,
    ) -> Invoice:
        today = today or date.today()
        inv_id, number = self.store.next_number()
        invoice = Invoice(
            id=inv_id,
            number=number,
            client_name=client_name,
            description=description,
            subtotal=money(amount),
            tax_rate=tax_rate,
            due_date=due_date or (today + timedelta(days=DEFAULT_DUE_DAYS)),
        )
        invoice.add_event("created", f"Invoice {number} created")
        return self.store.add(invoice)

    # ------------------------------------------------------------------
    # Tool 2: create_paypal_order
    # ------------------------------------------------------------------
    def create_paypal_order(self, invoice: Invoice) -> list[dict]:
        order = self.paypal.create_order(invoice)
        invoice.paypal_order_id = order.order_id
        invoice.pay_link = order.approve_link
        invoice.status = "SENT"
        invoice.add_event(
            "order",
            f"PayPal order {order.order_id} created; payment link sent to "
            f"{invoice.client_name}",
        )
        return [
            _step(
                "create_paypal_order",
                f"PayPal payment link created for {invoice.number}",
                f"Order {order.order_id} · ${fmt(invoice.total)} "
                f"({self.paypal.name} mode)",
            )
        ]

    # ------------------------------------------------------------------
    # Tool 3: check_status
    # ------------------------------------------------------------------
    def check_status(self, invoice: Invoice) -> list[dict]:
        if not invoice.paypal_order_id:
            return [_step("check_status", f"{invoice.number} has no PayPal order yet")]
        try:
            order = self.paypal.get_order(invoice.paypal_order_id)
            steps = [
                _step(
                    "check_status",
                    f"PayPal reports order {order.order_id}: {order.status}",
                )
            ]
            if order.status == "APPROVED":
                # The buyer approved the payment; settling it is the
                # merchant's move. Capture, then the money is in.
                order = self.paypal.capture_order(order.order_id)
                steps.append(
                    _step(
                        "check_status",
                        f"Captured payment for {invoice.number}",
                        f"Order {order.order_id} now {order.status}",
                    )
                )
        except (PayPalClientError, httpx.HTTPError) as exc:
            # A flaky PayPal call must never crash the loop — report it
            # as a step and let the next check try again.
            return [
                _step(
                    "check_status",
                    f"Could not reach PayPal for {invoice.number}",
                    str(exc),
                )
            ]
        if order.status == "COMPLETED" and invoice.status != "PAID":
            invoice.status = "PAID"
            invoice.paid_at = datetime.now(timezone.utc)
            invoice.add_event(
                "paid", f"Payment received — ${fmt(invoice.total)} from "
                f"{invoice.client_name}"
            )
            steps.append(
                _step("check_status", f"{invoice.number} marked PAID", "Receipt confirmed")
            )
        elif order.status == "VOIDED":
            steps.append(
                _step(
                    "check_status",
                    f"Order {order.order_id} was voided",
                    "A fresh payment link is needed",
                )
            )
        return steps

    # ------------------------------------------------------------------
    # Tool 4: send_reminder
    # ------------------------------------------------------------------
    def send_reminder(
        self, invoice: Invoice, today: date | None = None
    ) -> tuple[str, list[dict]]:
        today = today or date.today()
        status = invoice.effective_status(today)
        days_late = (today - invoice.due_date).days
        pay_clause = (
            f" You can pay securely here: {invoice.pay_link} — it takes "
            "under a minute."
            if invoice.pay_link
            else ""
        )
        # Escalation ladder: the longer an invoice is overdue, the
        # firmer the nudge — always polite, always exact on the facts.
        # Tiers mirror the aging buckets in reporting.py.
        if invoice.status == "PAID":
            tone = "settled"
            message = (
                f"Hi {invoice.client_name} — good news, invoice "
                f"{invoice.number} is already settled. Thank you!"
            )
        elif status == "OVERDUE" and days_late >= 31:
            tone = "final"
            message = (
                f"Hi {invoice.client_name}, this is a final notice for "
                f"invoice {invoice.number} for ${fmt(invoice.total)} "
                f"({invoice.description}), due {invoice.due_date.isoformat()} "
                f"and now {days_late} days overdue.{pay_clause} Please pay, "
                f"or contact us within 7 days so we can resolve this "
                f"together, before we pause further work and review next "
                f"steps. Thank you for your attention."
            )
        elif status == "OVERDUE" and days_late >= 8:
            tone = "firm"
            message = (
                f"Hi {invoice.client_name}, following up on invoice "
                f"{invoice.number} for ${fmt(invoice.total)} "
                f"({invoice.description}), due {invoice.due_date.isoformat()} "
                f"and now {days_late} days overdue. Please arrange payment "
                f"this week.{pay_clause} If payment is already on its way, "
                f"thank you — a quick note with the date helps us reconcile."
            )
        elif status == "OVERDUE":
            tone = "gentle"
            message = (
                f"Hi {invoice.client_name}, a friendly nudge: invoice "
                f"{invoice.number} for ${fmt(invoice.total)} "
                f"({invoice.description}) was due {invoice.due_date.isoformat()} "
                f"and is now {days_late} day{'s' if days_late != 1 else ''} "
                f"overdue.{pay_clause} If you've already paid, just "
                f"ignore this. Thank you!"
            )
        else:
            tone = "upcoming"
            message = (
                f"Hi {invoice.client_name}, a quick heads-up: invoice "
                f"{invoice.number} for ${fmt(invoice.total)} "
                f"({invoice.description}) is due {invoice.due_date.isoformat()}."
                f"{pay_clause} Thank you!"
            )
        record = {
            "at": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "tone": tone,
            "channel": "mock-inbox" if isinstance(self.paypal, MockPayPalClient) else "email",
        }
        invoice.reminders.append(record)
        invoice.add_event(
            "reminder",
            f"Reminder sent to {invoice.client_name} ({tone} tone)",
        )
        return message, [
            _step(
                "send_reminder",
                f"Reminder sent to {invoice.client_name}",
                f"Tone: {tone}"
                + (f" · {days_late} days overdue" if status == "OVERDUE" else ""),
            )
        ]

    # ------------------------------------------------------------------
    # Tool 5: reconcile
    # ------------------------------------------------------------------
    def reconcile(self, today: date | None = None) -> dict:
        today = today or date.today()
        invoices = self.store.all()
        paid = [i for i in invoices if i.status == "PAID"]
        outstanding = [i for i in invoices if i.status != "PAID"]
        overdue = [i for i in outstanding if i.effective_status(today) == "OVERDUE"]
        total_invoiced = sum((i.total for i in invoices), Decimal("0"))
        total_paid = sum((i.total for i in paid), Decimal("0"))
        total_outstanding = sum((i.total for i in outstanding), Decimal("0"))
        total_overdue = sum((i.total for i in overdue), Decimal("0"))
        summary = (
            f"{len(invoices)} invoice(s): ${fmt(total_invoiced)} invoiced, "
            f"${fmt(total_paid)} collected, ${fmt(total_outstanding)} "
            f"outstanding"
            + (f", of which ${fmt(total_overdue)} is overdue." if overdue else ".")
        )
        return {
            "as_of": today.isoformat(),
            "invoices": len(invoices),
            "paid_count": len(paid),
            "outstanding_count": len(outstanding),
            "overdue_count": len(overdue),
            "total_invoiced": fmt(total_invoiced),
            "total_paid": fmt(total_paid),
            "total_outstanding": fmt(total_outstanding),
            "total_overdue": fmt(total_overdue),
            "paid": [
                {"number": i.number, "client": i.client_name, "total": fmt(i.total)}
                for i in paid
            ],
            "overdue": [
                {"number": i.number, "client": i.client_name, "total": fmt(i.total)}
                for i in overdue
            ],
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # The full loop from one plain-language message
    # ------------------------------------------------------------------
    def run_from_text(
        self, text: str, today: date | None = None
    ) -> tuple[Invoice, list[dict]]:
        invoice, steps = self.create_invoice(text, today=today)
        steps += self.create_paypal_order(invoice)
        steps += self.check_status(invoice)
        return invoice, steps
