"""Reporting for InvoicePilot: aging buckets, per-client summaries, CSV.

Pure functions over the invoice list, Decimal-exact like everything else
in this codebase. These feed the AG Grid reporting view in the web UI
(``GET /api/reports/*``) — the grid itself does sorting / filtering /
pagination in the browser; the numbers it displays come from here, so
they are unit-testable without a browser.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from .models import Invoice, fmt

# (key, label, min_days_overdue, max_days_overdue or None)
BUCKETS: tuple[tuple[str, str, int, int | None], ...] = (
    ("current", "Not yet due", 0, 0),
    ("1-7", "1–7 days overdue", 1, 7),
    ("8-30", "8–30 days overdue", 8, 30),
    ("31+", "31+ days overdue", 31, None),
)


def days_overdue(invoice: Invoice, today: date) -> int:
    """Whole days past the due date; 0 for paid or not-yet-due invoices."""
    if invoice.status == "PAID":
        return 0
    return max(0, (today - invoice.due_date).days)


def _bucket_for(days: int) -> str:
    for key, _label, lo, hi in BUCKETS:
        if days >= lo and (hi is None or days <= hi):
            return key
    return "31+"  # pragma: no cover - days is always >= 0


def aging_report(invoices: list[Invoice], today: date | None = None) -> dict:
    """Outstanding money grouped by how late it is.

    Paid invoices are excluded — aging is about money still owed. Bucket
    boundaries mirror the reminder-escalation tiers in ``agent.py`` so
    the report and the agent's chasing behaviour tell the same story.
    """
    today = today or date.today()
    buckets: dict[str, dict] = {
        key: {
            "bucket": key,
            "label": label,
            "count": 0,
            "total": Decimal("0"),
            "invoices": [],
        }
        for key, label, _lo, _hi in BUCKETS
    }
    for inv in invoices:
        if inv.status == "PAID":
            continue
        days = days_overdue(inv, today)
        bucket = buckets[_bucket_for(days)]
        bucket["count"] += 1
        bucket["total"] += inv.total
        bucket["invoices"].append(inv.number)
    outstanding = sum((b["total"] for b in buckets.values()), Decimal("0"))
    return {
        "as_of": today.isoformat(),
        "buckets": [
            {
                "bucket": b["bucket"],
                "label": b["label"],
                "count": b["count"],
                "total": fmt(b["total"]),
                "invoices": b["invoices"],
            }
            for b in buckets.values()
        ],
        "outstanding_count": sum(b["count"] for b in buckets.values()),
        "total_outstanding": fmt(outstanding),
        "overdue_count": sum(
            b["count"] for k, b in buckets.items() if k != "current"
        ),
        "total_overdue": fmt(
            sum(
                (b["total"] for k, b in buckets.items() if k != "current"),
                Decimal("0"),
            )
        ),
    }


def client_summary(invoices: list[Invoice], today: date | None = None) -> dict:
    """Per-client roll-up: invoiced / collected / outstanding / overdue."""
    today = today or date.today()
    by_client: dict[str, dict] = {}
    for inv in invoices:
        row = by_client.setdefault(
            inv.client_name,
            {
                "client": inv.client_name,
                "invoices": 0,
                "invoiced": Decimal("0"),
                "paid": Decimal("0"),
                "outstanding": Decimal("0"),
                "overdue": Decimal("0"),
            },
        )
        row["invoices"] += 1
        row["invoiced"] += inv.total
        if inv.status == "PAID":
            row["paid"] += inv.total
        else:
            row["outstanding"] += inv.total
            if days_overdue(inv, today) > 0:
                row["overdue"] += inv.total
    rows = sorted(
        by_client.values(),
        key=lambda r: (r["outstanding"], r["invoiced"]),
        reverse=True,
    )
    return {
        "as_of": today.isoformat(),
        "clients": [
            {
                "client": r["client"],
                "invoices": r["invoices"],
                "total_invoiced": fmt(r["invoiced"]),
                "total_paid": fmt(r["paid"]),
                "total_outstanding": fmt(r["outstanding"]),
                "total_overdue": fmt(r["overdue"]),
            }
            for r in rows
        ],
    }


def invoices_csv(invoices: list[Invoice], today: date | None = None) -> str:
    """The full books as CSV — the same rows the AG Grid view exports."""
    today = today or date.today()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "number",
            "client",
            "description",
            "status",
            "subtotal",
            "tax_rate_percent",
            "tax_amount",
            "total",
            "currency",
            "due_date",
            "days_overdue",
            "paid_at",
            "paypal_order_id",
        ]
    )
    for inv in invoices:
        writer.writerow(
            [
                inv.number,
                inv.client_name,
                inv.description,
                inv.effective_status(today),
                fmt(inv.subtotal),
                str(inv.tax_rate),
                fmt(inv.tax_amount),
                fmt(inv.total),
                inv.currency,
                inv.due_date.isoformat(),
                days_overdue(inv, today),
                inv.paid_at.isoformat() if inv.paid_at else "",
                inv.paypal_order_id or "",
            ]
        )
    return buf.getvalue()
