"""Tests for the Week-1 database store (app/db_store.py).

These are NEW tests for the persistence layer. The original 62 tests keep
running against the in-memory store, unchanged. The DB tests run on SQLite
(the dev/test dialect — no Postgres server on this machine); the same
SQLAlchemy models target Postgres in production.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.db import create_all, make_engine, make_session_factory
from app.db_store import DbInvoiceStore
from app.models import Invoice


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    create_all(engine)
    return make_session_factory(engine)


@pytest.fixture()
def store(session_factory):
    return DbInvoiceStore(owner_id="owner-a", session_factory=session_factory)


def make_invoice(store, **overrides):
    inv_id, number = store.next_number()
    fields = dict(
        id=inv_id,
        number=number,
        client_name="Maria's Bakery",
        description="Logo design",
        subtotal=Decimal("450.00"),
        tax_rate=Decimal("8.25"),
        currency="USD",
        due_date=date(2026, 11, 20),
        source_text="Logo design for Maria's bakery, $450",
    )
    fields.update(overrides)
    invoice = Invoice(**fields)
    invoice.add_event("created", "Invoice created")
    return invoice


def test_add_get_roundtrip(store):
    invoice = make_invoice(store)
    invoice.paypal_order_id = "ORDER-123"
    invoice.pay_link = "https://example.test/pay/ORDER-123"
    invoice.add_event("order", "PayPal order created")
    store.add(invoice)

    loaded = store.get(invoice.id)
    assert loaded.id == invoice.id
    assert loaded.number == "INV-1001"
    assert loaded.client_name == "Maria's Bakery"
    assert loaded.description == "Logo design"
    assert loaded.subtotal == Decimal("450.00")
    assert loaded.tax_rate == Decimal("8.25")
    assert loaded.tax_amount == Decimal("37.13")  # 450 * 8.25% = 37.125 -> 37.13
    assert loaded.total == Decimal("487.13")
    assert loaded.currency == "USD"
    assert loaded.due_date == date(2026, 11, 20)
    assert loaded.status == "DRAFT"
    assert loaded.paypal_order_id == "ORDER-123"
    assert loaded.pay_link == "https://example.test/pay/ORDER-123"
    assert loaded.source_text == "Logo design for Maria's bakery, $450"
    assert [e.kind for e in loaded.timeline] == ["created", "order"]
    assert loaded.timeline[0].message == "Invoice created"


def test_money_roundtrips_exactly_to_the_cent(store):
    invoice = make_invoice(
        store, subtotal=Decimal("19.99"), tax_rate=Decimal("7.5")
    )
    store.add(invoice)
    loaded = store.get(invoice.id)
    assert loaded.subtotal == Decimal("19.99")
    assert loaded.tax_amount == Decimal("1.50")  # 1.49925 -> 1.50
    assert loaded.total == Decimal("21.49")
    assert loaded.to_dict()["total"] == "21.49"


def test_update_flow_via_save(store, session_factory):
    invoice = make_invoice(store)
    store.add(invoice)

    invoice.status = "SENT"
    invoice.add_event("status", "Invoice sent")
    invoice.reminders.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "message": "Hi Maria — a quick heads-up",
            "channel": "email",
        }
    )
    store.save(invoice)

    # A fresh store instance (new sessions) sees the updated state.
    fresh = DbInvoiceStore(owner_id="owner-a", session_factory=session_factory)
    loaded = fresh.get(invoice.id)
    assert loaded.status == "SENT"
    assert [e.kind for e in loaded.timeline] == ["created", "status"]
    assert len(loaded.reminders) == 1
    assert loaded.reminders[0]["channel"] == "email"
    assert loaded.reminders[0]["message"] == "Hi Maria — a quick heads-up"

    loaded.status = "PAID"
    loaded.paid_at = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    fresh.save(loaded)
    reloaded = fresh.get(invoice.id)
    assert reloaded.status == "PAID"
    assert reloaded.paid_at == datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def test_idempotent_replay_returns_original(store, session_factory):
    invoice = make_invoice(store)
    store.add(invoice)
    store.remember_idempotency_key("req-abc-1", invoice)

    fresh = DbInvoiceStore(owner_id="owner-a", session_factory=session_factory)
    replayed = fresh.by_idempotency_key("req-abc-1")
    assert replayed is not None
    assert replayed.id == invoice.id
    assert replayed.number == invoice.number
    assert replayed.total == invoice.total
    assert fresh.by_idempotency_key("never-seen") is None


def test_cross_owner_isolation(session_factory):
    owner_a = DbInvoiceStore(owner_id="owner-a", session_factory=session_factory)
    owner_b = DbInvoiceStore(owner_id="owner-b", session_factory=session_factory)

    invoice = make_invoice(owner_a)
    invoice.paypal_order_id = "ORDER-A-ONLY"
    owner_a.add(invoice)
    owner_a.remember_idempotency_key("a-key", invoice)

    assert owner_b.all() == []
    with pytest.raises(KeyError):
        owner_b.get(invoice.id)
    assert owner_b.by_idempotency_key("a-key") is None
    assert owner_b.by_order("ORDER-A-ONLY") is None
    # ...while the owner still sees everything.
    assert owner_a.get(invoice.id).client_name == "Maria's Bakery"


def test_next_number_sequences_are_per_owner(session_factory):
    owner_a = DbInvoiceStore(owner_id="owner-a", session_factory=session_factory)
    owner_b = DbInvoiceStore(owner_id="owner-b", session_factory=session_factory)

    assert owner_a.next_number() == ("inv-0001", "INV-1001")
    assert owner_a.next_number() == ("inv-0002", "INV-1002")
    assert owner_b.next_number() == ("inv-0001", "INV-1001")


def test_by_order_lookup(store):
    invoice = make_invoice(store)
    invoice.paypal_order_id = "ORDER-XYZ"
    store.add(invoice)

    found = store.by_order("ORDER-XYZ")
    assert found is not None and found.id == invoice.id
    assert store.by_order("ORDER-MISSING") is None


def test_all_returns_only_own_invoices_in_order(store):
    first = make_invoice(store)
    second = make_invoice(store, client_name="Other Client")
    store.add(first)
    store.add(second)
    assert [i.id for i in store.all()] == [first.id, second.id]


def test_data_survives_a_brand_new_engine(tmp_path):
    db_url = f"sqlite:///{tmp_path}/durable.db"
    engine1 = make_engine(db_url)
    create_all(engine1)
    writer = DbInvoiceStore(
        owner_id="owner-a", session_factory=make_session_factory(engine1)
    )
    invoice = make_invoice(writer)
    writer.add(invoice)

    engine2 = make_engine(db_url)  # new engine, same file: truly persisted
    reader = DbInvoiceStore(
        owner_id="owner-a", session_factory=make_session_factory(engine2)
    )
    loaded = reader.get(invoice.id)
    assert loaded.number == "INV-1001"
    assert loaded.total == Decimal("487.13")
