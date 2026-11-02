"""Reminder escalation ladder: gentle -> firm -> final by days overdue.

The gentle tier (1-7 days) keeps the original wording — test_payments
pins it. These tests pin the new tiers + the tone recorded per reminder.
Fixed 'today' = Monday 2026-11-02.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.agent import InvoicePilotAgent
from app.config import Settings
from app.paypal_client import MockPayPalClient
from app.store import InvoiceStore

TODAY = date(2026, 11, 2)


@pytest.fixture
def env():
    store = InvoiceStore()
    agent = InvoicePilotAgent(Settings(), store, MockPayPalClient())
    return agent


def make(agent, *, due, amount="100"):
    inv = agent.create_invoice_structured(
        description="Work",
        client_name="Client A",
        amount=Decimal(amount),
        due_date=due,
        today=TODAY,
    )
    agent.create_paypal_order(inv)
    return inv


def test_gentle_tier_keeps_original_wording(env):
    inv = make(env, due=date(2026, 10, 30))  # 3 days overdue
    message, _ = env.send_reminder(inv, today=TODAY)
    assert "a friendly nudge" in message
    assert "3 days overdue" in message
    assert inv.reminders[-1]["tone"] == "gentle"


def test_firm_tier_at_8_to_30_days(env):
    inv = make(env, due=date(2026, 10, 20))  # 13 days overdue
    message, _ = env.send_reminder(inv, today=TODAY)
    assert "13 days overdue" in message
    assert "arrange payment this week" in message
    assert inv.reminders[-1]["tone"] == "firm"


def test_final_tier_at_31_plus_days(env):
    inv = make(env, due=date(2026, 9, 15))  # 48 days overdue
    message, _ = env.send_reminder(inv, today=TODAY)
    assert "final notice" in message
    assert "48 days overdue" in message
    assert inv.reminders[-1]["tone"] == "final"


def test_upcoming_and_boundary_tones(env):
    upcoming = make(env, due=date(2026, 11, 9))
    _, _ = env.send_reminder(upcoming, today=TODAY)
    assert upcoming.reminders[-1]["tone"] == "upcoming"

    day7 = make(env, due=date(2026, 10, 26))  # exactly 7 -> still gentle
    env.send_reminder(day7, today=TODAY)
    assert day7.reminders[-1]["tone"] == "gentle"

    day8 = make(env, due=date(2026, 10, 25))  # exactly 8 -> firm
    env.send_reminder(day8, today=TODAY)
    assert day8.reminders[-1]["tone"] == "firm"

    day31 = make(env, due=date(2026, 10, 2))  # exactly 31 -> final
    env.send_reminder(day31, today=TODAY)
    assert day31.reminders[-1]["tone"] == "final"
