"""PayPal webhooks: approved -> capture -> PAID, dedupe, edge cases.

Uses the app's TestClient in mock mode plus the processor directly.
Fixed dates come from the live app (mock flow), so assertions stick to
status transitions, not calendar math.
"""
import pytest
from fastapi.testclient import TestClient

from app.agent import InvoicePilotAgent
from app.config import Settings
from app.main import app
from app.paypal_client import MockPayPalClient
from app.store import InvoiceStore
from app.webhooks import PayPalWebhookProcessor

client = TestClient(app)


def test_processor_captures_on_order_approved_event():
    """The headline flow: buyer approves on PayPal, the webhook fires,
    and the agent — not a poll — captures and marks the invoice PAID."""
    store = InvoiceStore()
    paypal = MockPayPalClient()
    agent = InvoicePilotAgent(Settings(), store, paypal)
    processor = PayPalWebhookProcessor(agent, store)

    invoice, _ = agent.run_from_text("Logo design for Maria, $450, due Friday")
    assert invoice.status == "SENT"
    paypal.simulate_payment(invoice.paypal_order_id)  # buyer approved only
    assert paypal.get_order(invoice.paypal_order_id).status == "APPROVED"
    assert invoice.status == "SENT"  # approval alone is not paid

    result = processor.handle(
        {
            "id": "WH-APPROVED-1",
            "event_type": "CHECKOUT.ORDER.APPROVED",
            "resource": {"id": invoice.paypal_order_id},
        }
    )
    assert result["invoice"]["status"] == "PAID"
    assert invoice.paid_at is not None
    assert paypal.get_order(invoice.paypal_order_id).status == "COMPLETED"


def test_processor_records_denied_event_on_timeline():
    store = InvoiceStore()
    paypal = MockPayPalClient()
    agent = InvoicePilotAgent(Settings(), store, paypal)
    processor = PayPalWebhookProcessor(agent, store)
    invoice, _ = agent.run_from_text("Cake for Priya, $85, due yesterday")
    result = processor.handle(
        {
            "id": "WH-DENIED-1",
            "event_type": "PAYMENT.CAPTURE.DENIED",
            "resource": {
                "supplementary_data": {
                    "related_ids": {"order_id": invoice.paypal_order_id}
                }
            },
        }
    )
    assert result["invoice"]["status"] != "PAID"
    assert any(
        "PAYMENT.CAPTURE.DENIED" in e["message"]
        for e in result["invoice"]["timeline"]
    )


def _create_paid_via_webhook() -> dict:
    r = client.post(
        "/api/invoices/from-text",
        json={"text": "Logo design for Maria's bakery, $450, due Friday"},
    )
    assert r.status_code == 200
    return r.json()["invoice"]


def _pay_via_mock_checkout(invoice: dict) -> None:
    r = client.post(f"/api/invoices/{invoice['id']}/simulate-pay")
    assert r.status_code == 200


def test_webhook_capture_completed_marks_paid():
    inv = _create_paid_via_webhook()
    _pay_via_mock_checkout(inv)  # buyer approved; agent captured on poll
    # A capture-completed webhook for the same order is consistent:
    # invoice already PAID, event acknowledged, no error, no double-pay.
    event = {
        "id": "WH-CAPTURE-1",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAPTURE-1",
            "supplementary_data": {
                "related_ids": {"order_id": inv["paypal_order_id"]}
            },
        },
    }
    r = client.post("/api/paypal/webhook", json=event)
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    assert body["duplicate"] is False
    assert body["verified"] is False  # demo mode: labelled, not faked
    assert body["invoice"]["status"] == "PAID"


def test_webhook_duplicate_delivery_is_ignored():
    event = {
        "id": "WH-DUP-1",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": "NO-SUCH-ORDER"},
    }
    first = client.post("/api/paypal/webhook", json=event).json()
    second = client.post("/api/paypal/webhook", json=event).json()
    assert first["duplicate"] is False
    assert second["duplicate"] is True


def test_webhook_unknown_order_acknowledged():
    event = {
        "id": "WH-UNKNOWN-1",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "supplementary_data": {"related_ids": {"order_id": "NOPE"}}
        },
    }
    r = client.post("/api/paypal/webhook", json=event)
    assert r.status_code == 200
    assert r.json()["invoice"] is None


def test_webhook_unhandled_type_acknowledged():
    event = {"id": "WH-OTHER-1", "event_type": "BILLING.SUBSCRIPTION.CREATED"}
    r = client.post("/api/paypal/webhook", json=event)
    assert r.status_code == 200
    assert r.json()["received"] is True


def test_webhook_missing_fields_rejected():
    r = client.post("/api/paypal/webhook", json={"event_type": "X"})
    assert r.status_code == 422


def test_reports_endpoints_over_http():
    _create_paid_via_webhook()
    aging = client.get("/api/reports/aging")
    assert aging.status_code == 200
    body = aging.json()
    assert {b["bucket"] for b in body["buckets"]} == {
        "current",
        "1-7",
        "8-30",
        "31+",
    }
    clients = client.get("/api/reports/clients")
    assert clients.status_code == 200
    assert "clients" in clients.json()
    csv_resp = client.get("/api/reports/export.csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.splitlines()[0].startswith("number,client,")


def test_health_advertises_new_features():
    body = client.get("/api/health").json()
    assert "ag-grid-reporting" in body["features"]
    assert "paypal-webhooks" in body["features"]
    assert body["webhook_verification"] == "demo-unverified"
