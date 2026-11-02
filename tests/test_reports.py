"""Reporting: aging buckets, per-client summary, CSV export.

Fixed 'today' = Monday 2026-11-02 (same convention as test_payments).
All money assertions are exact strings — Decimal end to end.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.agent import InvoicePilotAgent
from app.config import Settings
from app.paypal_client import MockPayPalClient
from app.reporting import aging_report, client_summary, invoices_csv
from app.store import InvoiceStore

TODAY = date(2026, 11, 2)


@pytest.fixture
def env():
    store = InvoiceStore()
    client = MockPayPalClient()
    agent = InvoicePilotAgent(Settings(), store, client)
    return agent, store, client


def make(agent, *, amount="100", due=date(2026, 11, 9), client="Client A"):
    inv = agent.create_invoice_structured(
        description="Work",
        client_name=client,
        amount=Decimal(amount),
        due_date=due,
        today=TODAY,
    )
    agent.create_paypal_order(inv)
    return inv


def test_aging_buckets_split_outstanding_by_lateness(env):
    agent, store, client = env
    make(agent, amount="10", due=date(2026, 11, 9))           # not yet due
    make(agent, amount="20", due=date(2026, 11, 2))           # due today -> current
    make(agent, amount="30", due=date(2026, 10, 30))          # 3 days -> 1-7
    make(agent, amount="40", due=date(2026, 10, 20))          # 13 days -> 8-30
    make(agent, amount="50", due=date(2026, 9, 1))            # 62 days -> 31+
    paid = make(agent, amount="60", due=date(2026, 9, 1))     # paid -> excluded
    client.simulate_payment(paid.paypal_order_id)
    agent.check_status(paid)

    report = aging_report(store.all(), today=TODAY)
    by_key = {b["bucket"]: b for b in report["buckets"]}
    assert by_key["current"]["count"] == 2
    assert by_key["current"]["total"] == "30.00"
    assert by_key["1-7"]["total"] == "30.00"
    assert by_key["8-30"]["total"] == "40.00"
    assert by_key["31+"]["total"] == "50.00"
    assert report["outstanding_count"] == 5
    assert report["total_outstanding"] == "150.00"
    assert report["overdue_count"] == 3
    assert report["total_overdue"] == "120.00"


def test_client_summary_rolls_up_per_client(env):
    agent, store, client = env
    make(agent, amount="100", client="Maria", due=date(2026, 10, 30))
    paid = make(agent, amount="450", client="Maria")
    client.simulate_payment(paid.paypal_order_id)
    agent.check_status(paid)
    make(agent, amount="85", client="Priya", due=date(2026, 11, 1))

    summary = client_summary(store.all(), today=TODAY)
    rows = {r["client"]: r for r in summary["clients"]}
    assert rows["Maria"]["invoices"] == 2
    assert rows["Maria"]["total_invoiced"] == "550.00"
    assert rows["Maria"]["total_paid"] == "450.00"
    assert rows["Maria"]["total_outstanding"] == "100.00"
    assert rows["Maria"]["total_overdue"] == "100.00"
    assert rows["Priya"]["total_overdue"] == "85.00"
    # Sorted by outstanding desc: Maria (100) before Priya (85)
    assert summary["clients"][0]["client"] == "Maria"


def test_csv_export_has_header_and_exact_rows(env):
    agent, store, _ = env
    make(agent, amount="85", client="Priya", due=date(2026, 11, 1))
    text = invoices_csv(store.all(), today=TODAY)
    lines = text.strip().splitlines()
    assert lines[0].startswith("number,client,description,status,")
    assert len(lines) == 2
    assert "Priya" in lines[1] and "85.00" in lines[1]
    assert ",OVERDUE," in lines[1] and ",1," in lines[1]  # 1 day overdue
