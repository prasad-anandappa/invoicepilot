"""Payment state transitions, capture lifecycle, reconcile boundaries.

Agent-level tests with a fresh store + mock client per test, so totals
are exact. Fixed 'today' = Monday 2026-11-02.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.agent import InvoicePilotAgent
from app.config import Settings
from app.paypal_client import MockPayPalClient, PayPalClientError
from app.store import InvoiceStore

TODAY = date(2026, 11, 2)


@pytest.fixture
def env():
    store = InvoiceStore()
    client = MockPayPalClient()
    agent = InvoicePilotAgent(Settings(), store, client)
    return agent, store, client


def make_sent(agent, *, amount="100", due=date(2026, 11, 9), client="Client A"):
    inv = agent.create_invoice_structured(
        description="Work",
        client_name=client,
        amount=Decimal(amount),
        due_date=due,
        today=TODAY,
    )
    agent.create_paypal_order(inv)
    return inv


# ----------------------------------------------------------------------
# Mock client lifecycle: CREATED -> APPROVED -> COMPLETED
# ----------------------------------------------------------------------

def test_mock_order_lifecycle(env):
    agent, _, client = env
    inv = make_sent(agent)
    assert client.get_order(inv.paypal_order_id).status == "CREATED"

    approved = client.simulate_payment(inv.paypal_order_id)
    assert approved.status == "APPROVED"  # approval alone is not settled

    captured = client.capture_order(inv.paypal_order_id)
    assert captured.status == "COMPLETED"
    # capture is idempotent
    assert client.capture_order(inv.paypal_order_id).status == "COMPLETED"


def test_capture_before_approval_rejected(env):
    agent, _, client = env
    inv = make_sent(agent)
    with pytest.raises(PayPalClientError):
        client.capture_order(inv.paypal_order_id)


def test_unknown_order_raises_domain_error(env):
    _, _, client = env
    with pytest.raises(PayPalClientError):
        client.get_order("MOCK-ORDER-9999")
    with pytest.raises(PayPalClientError):
        client.simulate_payment("MOCK-ORDER-9999")
    with pytest.raises(PayPalClientError):
        client.capture_order("MOCK-ORDER-9999")


# ----------------------------------------------------------------------
# Agent check_status: capture + idempotency + failure tolerance
# ----------------------------------------------------------------------

def test_check_status_captures_approved_order(env):
    agent, _, client = env
    inv = make_sent(agent, amount="450")
    client.simulate_payment(inv.paypal_order_id)

    steps = agent.check_status(inv)
    summaries = [s["summary"] for s in steps]
    assert inv.status == "PAID"
    assert inv.paid_at is not None
    assert any("Captured payment" in s for s in summaries)


def test_check_status_is_idempotent_once_paid(env):
    agent, _, client = env
    inv = make_sent(agent)
    client.simulate_payment(inv.paypal_order_id)
    agent.check_status(inv)
    paid_at, events = inv.paid_at, len(inv.timeline)

    agent.check_status(inv)
    agent.check_status(inv)
    assert inv.paid_at == paid_at  # not re-stamped
    assert len(inv.timeline) == events  # no duplicate 'paid' events


def test_check_status_without_order_is_a_step_not_a_crash(env):
    agent, _, _ = env
    inv = agent.create_invoice_structured(
        description="Work",
        client_name="Solo",
        amount=Decimal("10"),
        due_date=date(2026, 11, 9),
        today=TODAY,
    )
    steps = agent.check_status(inv)
    assert "no PayPal order" in steps[0]["summary"]


def test_check_status_survives_paypal_failure(env, monkeypatch):
    agent, _, client = env
    inv = make_sent(agent)

    def boom(order_id):
        raise PayPalClientError("sandbox is down")

    monkeypatch.setattr(client, "get_order", boom)
    steps = agent.check_status(inv)
    assert "Could not reach PayPal" in steps[0]["summary"]
    assert inv.status == "SENT"  # unchanged, retryable next check


# ----------------------------------------------------------------------
# Reconcile boundaries + the demo scenario
# ----------------------------------------------------------------------

def test_reconcile_boundaries(env):
    agent, _, client = env
    paid = make_sent(agent, amount="450", due=date(2026, 11, 6), client="Maria")
    client.simulate_payment(paid.paypal_order_id)
    agent.check_status(paid)

    make_sent(agent, amount="85", due=date(2026, 11, 1), client="Priya")  # overdue
    make_sent(agent, amount="100", due=TODAY, client="DueToday")  # NOT overdue
    make_sent(agent, amount="50", due=date(2026, 11, 7), client="Later")

    rec = agent.reconcile(today=TODAY)
    assert rec["total_invoiced"] == "685.00"
    assert rec["total_paid"] == "450.00"
    assert rec["total_outstanding"] == "235.00"
    assert rec["total_overdue"] == "85.00"
    assert rec["overdue_count"] == 1
    assert rec["paid_count"] == 1


def test_demo_scenario_totals(env):
    """The verified demo: $535 invoiced / $450 collected / $85 overdue."""
    agent, _, client = env
    maria, _ = agent.run_from_text(
        "Logo design for Maria's bakery, $450, due Friday", today=TODAY
    )
    client.simulate_payment(maria.paypal_order_id)
    agent.check_status(maria)
    agent.run_from_text(
        "Birthday cake order for Priya, $85, due yesterday", today=TODAY
    )
    rec = agent.reconcile(today=TODAY)
    assert rec["total_invoiced"] == "535.00"
    assert rec["total_paid"] == "450.00"
    assert rec["total_overdue"] == "85.00"


# ----------------------------------------------------------------------
# Reminders: tone + facts per state
# ----------------------------------------------------------------------

def test_reminder_for_paid_invoice_says_settled(env):
    agent, _, client = env
    inv = make_sent(agent)
    client.simulate_payment(inv.paypal_order_id)
    agent.check_status(inv)
    message, _ = agent.send_reminder(inv, today=TODAY)
    assert "already settled" in message


def test_reminder_overdue_day_pluralisation(env):
    agent, _, _ = env
    one = make_sent(agent, amount="85", due=date(2026, 11, 1), client="Priya")
    msg_one, _ = agent.send_reminder(one, today=TODAY)
    assert "1 day overdue" in msg_one

    three = make_sent(agent, amount="85", due=date(2026, 10, 30), client="Raj")
    msg_three, _ = agent.send_reminder(three, today=TODAY)
    assert "3 days overdue" in msg_three


def test_reminder_without_pay_link_has_no_dangling_link(env):
    agent, _, _ = env
    inv = agent.create_invoice_structured(
        description="Work",
        client_name="NoLink",
        amount=Decimal("40"),
        due_date=date(2026, 11, 1),
        today=TODAY,
    )  # never ordered -> no pay link
    message, _ = agent.send_reminder(inv, today=TODAY)
    assert "pay securely here" not in message.lower()
    assert "NoLink" in message


def test_duplicate_text_requests_create_separate_invoices(env):
    """Without an idempotency key, asking twice = two invoices (the API
    layer deduplicates only when the caller supplies a key)."""
    agent, store, _ = env
    agent.run_from_text("Logo design for Maria, $450, due Friday", today=TODAY)
    agent.run_from_text("Logo design for Maria, $450, due Friday", today=TODAY)
    assert len(store.all()) == 2
