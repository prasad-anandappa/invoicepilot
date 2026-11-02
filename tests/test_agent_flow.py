"""End-to-end agent flow in MOCK mode, through the real HTTP API."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_mock_mode():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["paypal_mode"] == "mock"
    assert body["llm"] == "rule-based fallback"


def test_full_get_paid_loop():
    # 1. Plain language -> invoice + PayPal link, agent steps visible
    r = client.post(
        "/api/invoices/from-text",
        json={"text": "Logo design for Maria's bakery, $450, due Friday"},
    )
    assert r.status_code == 200
    data = r.json()
    inv = data["invoice"]
    assert inv["client_name"] == "Maria's bakery"
    assert inv["total"] == "450.00"
    assert inv["status"] == "SENT"
    assert inv["pay_link"].startswith("/mock-pay/")
    tools = [s["tool"] for s in data["steps"]]
    assert tools[0] == "create_invoice"
    assert "create_paypal_order" in tools

    # 2. Buyer pays on the simulated checkout
    r = client.post(f"/api/invoices/{inv['id']}/simulate-pay")
    assert r.status_code == 200
    assert r.json()["invoice"]["status"] == "PAID"

    # 3. Status check agrees, reconciliation counts the money
    r = client.post(f"/api/invoices/{inv['id']}/check-status")
    assert r.json()["invoice"]["status"] == "PAID"
    rec = client.get("/api/reconcile").json()
    assert rec["paid_count"] >= 1
    assert rec["total_paid"] == "450.00"


def test_overdue_reminder_flow():
    # An invoice that was due yesterday
    r = client.post(
        "/api/invoices/from-text",
        json={"text": "Birthday cake order for Priya, $85, due yesterday"},
    )
    inv = r.json()["invoice"]
    assert inv["status"] == "OVERDUE"

    r = client.post(f"/api/invoices/{inv['id']}/remind")
    assert r.status_code == 200
    msg = r.json()["reminder"]
    assert "Priya" in msg and "$85.00" in msg and "overdue" in msg.lower()

    rec = client.get("/api/reconcile").json()
    assert rec["overdue_count"] >= 1
    assert rec["total_outstanding"] == "85.00"


def test_missing_amount_rejected():
    r = client.post(
        "/api/invoices/from-text", json={"text": "Some work for someone"}
    )
    assert r.status_code == 422


def test_unknown_invoice_404():
    assert client.get("/api/invoices/nope").status_code == 404
