"""Invoice math tests — Decimal-exact totals, tax and rounding."""
from datetime import date
from decimal import Decimal

from app.models import Invoice, money


def make_invoice(subtotal: str, tax_rate: str) -> Invoice:
    return Invoice(
        id="inv-test",
        number="INV-TEST",
        client_name="Test Client",
        description="Test work",
        subtotal=money(subtotal),
        tax_rate=Decimal(tax_rate),
        due_date=date(2026, 11, 9),
    )


def test_no_tax():
    inv = make_invoice("450", "0")
    assert inv.tax_amount == Decimal("0.00")
    assert inv.total == Decimal("450.00")


def test_eight_percent_tax():
    inv = make_invoice("1200", "8")
    assert inv.tax_amount == Decimal("96.00")
    assert inv.total == Decimal("1296.00")


def test_fractional_tax_rounds_half_up():
    inv = make_invoice("99.99", "8.25")
    # 99.99 * 0.0825 = 8.249175 -> 8.25
    assert inv.tax_amount == Decimal("8.25")
    assert inv.total == Decimal("108.24")


def test_money_quantises_to_cents():
    assert money("10.005") == Decimal("10.01")  # ROUND_HALF_UP
    assert money(10) == Decimal("10.00")


def test_overdue_is_derived_not_stored():
    inv = make_invoice("100", "0")
    inv.status = "SENT"
    inv.due_date = date(2026, 11, 1)
    assert inv.effective_status(date(2026, 11, 2)) == "OVERDUE"
    inv.status = "PAID"
    assert inv.effective_status(date(2026, 11, 2)) == "PAID"
