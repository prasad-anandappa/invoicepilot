"""Parser tests — deterministic, fixed 'today' = Monday 2026-11-02."""
from datetime import date
from decimal import Decimal

import pytest

from app.parser import parse_invoice_text

TODAY = date(2026, 11, 2)  # a Monday


def test_canonical_example():
    p = parse_invoice_text(
        "Logo design for Maria's bakery, $450, due Friday", today=TODAY
    )
    assert p.description == "Logo design"
    assert p.client_name == "Maria's bakery"
    assert p.amount == Decimal("450.00")
    assert p.tax_rate == Decimal("0")
    assert p.due_date == date(2026, 11, 6)  # the coming Friday


def test_tax_and_relative_days():
    p = parse_invoice_text(
        "Wedding photography for the Chen family, $1200 plus 8% tax, due in 3 days",
        today=TODAY,
    )
    assert p.client_name == "the Chen family"
    assert p.amount == Decimal("1200.00")
    assert p.tax_rate == Decimal("8")
    assert p.due_date == date(2026, 11, 5)


def test_month_name_due_date():
    p = parse_invoice_text(
        "Landing page build for FitWithRaj studio, $750, due Nov 20", today=TODAY
    )
    assert p.amount == Decimal("750.00")
    assert p.due_date == date(2026, 11, 20)


def test_due_yesterday_means_overdue_ready():
    p = parse_invoice_text("Birthday cake for Priya, $85, due yesterday", today=TODAY)
    assert p.due_date == date(2026, 11, 1)
    assert p.client_name == "Priya"


def test_dollars_word_and_commas():
    p = parse_invoice_text(
        "Consulting for Acme Corp, 1,250 dollars, due tomorrow", today=TODAY
    )
    assert p.amount == Decimal("1250.00")
    assert p.due_date == date(2026, 11, 3)


def test_friday_said_on_friday_rolls_a_week():
    friday = date(2026, 11, 6)
    p = parse_invoice_text("Design work for Jo, $100, due Friday", today=friday)
    assert p.due_date == date(2026, 11, 13)


def test_no_amount_is_an_error():
    with pytest.raises(ValueError):
        parse_invoice_text("Some work for someone, due Friday", today=TODAY)


def test_no_client_defaults():
    p = parse_invoice_text("Misc repairs, $60", today=TODAY)
    assert p.client_name == "Client"
    assert p.due_date is None  # caller applies the default
