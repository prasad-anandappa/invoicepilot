"""Parser edge cases — hostile and unusual natural-language input.

Fixed 'today' = Monday 2026-11-02 for every test.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.parser import parse_invoice_text

TODAY = date(2026, 11, 2)  # a Monday


def test_empty_and_whitespace_input_rejected():
    with pytest.raises(ValueError):
        parse_invoice_text("", today=TODAY)
    with pytest.raises(ValueError):
        parse_invoice_text("   \n  ", today=TODAY)


def test_zero_amount_rejected():
    with pytest.raises(ValueError):
        parse_invoice_text("Free sample for Maria, $0", today=TODAY)


def test_three_decimal_amount_rounds_half_up():
    p = parse_invoice_text("Logo design for Maria, $450.999", today=TODAY)
    assert p.amount == Decimal("451.00")
    # ...and no stray digit leaks into the description
    assert p.description == "Logo design"


def test_iso_due_date():
    p = parse_invoice_text("Audit for Delta, $900, due 2026-12-24", today=TODAY)
    assert p.due_date == date(2026, 12, 24)


def test_slash_date_with_year():
    p = parse_invoice_text("Audit for Delta, $900, due 12/24/2026", today=TODAY)
    assert p.due_date == date(2026, 12, 24)


def test_slash_date_far_past_rolls_to_next_year():
    p = parse_invoice_text("Audit for Delta, $900, due 1/5", today=TODAY)
    assert p.due_date == date(2027, 1, 5)


def test_month_name_far_past_rolls_to_next_year():
    p = parse_invoice_text("Audit for Delta, $900, due Oct 1", today=TODAY)
    assert p.due_date == date(2027, 10, 1)


def test_impossible_month_day_does_not_crash_or_clamp():
    # "Feb 30" is not a date; the parser must not silently turn it into
    # Feb 28 — it just finds no due date.
    p = parse_invoice_text("Work for A, $100, due Feb 30", today=TODAY)
    assert p.amount == Decimal("100.00")
    assert p.due_date is None


def test_due_today_and_day_after_tomorrow():
    p = parse_invoice_text("Rush job for Kim, $75, due today", today=TODAY)
    assert p.due_date == TODAY
    p = parse_invoice_text(
        "Rush job for Kim, $75, due day after tomorrow", today=TODAY
    )
    assert p.due_date == date(2026, 11, 4)


def test_due_in_zero_days_is_today():
    p = parse_invoice_text("Rush job for Kim, $75, due in 0 days", today=TODAY)
    assert p.due_date == TODAY


def test_weekday_with_by_phrasing():
    # Said on a Monday, "by Monday" means the coming Monday (a week out)
    p = parse_invoice_text("Finish the deck by Monday, $200 for Kim", today=TODAY)
    assert p.due_date == date(2026, 11, 9)


def test_usd_prefix_and_bucks_suffix():
    p = parse_invoice_text("Advisory for Beta LLC, USD 300", today=TODAY)
    assert p.amount == Decimal("300.00")
    assert p.client_name == "Beta LLC"
    p = parse_invoice_text("Advisory for Beta LLC, 300 bucks", today=TODAY)
    assert p.amount == Decimal("300.00")


def test_fractional_tax_rate():
    p = parse_invoice_text(
        "Repairs for Sam, $200 plus 8.25% tax", today=TODAY
    )
    assert p.tax_rate == Decimal("8.25")
    assert p.client_name == "Sam"


def test_absurd_tax_rate_rejected():
    with pytest.raises(ValueError):
        parse_invoice_text("Repairs for Sam, $200 plus 150% tax", today=TODAY)


def test_amount_first_capitalises_description():
    p = parse_invoice_text("$99 logo for Zed", today=TODAY)
    assert p.amount == Decimal("99.00")
    assert p.description == "Logo"
    assert p.client_name == "Zed"
