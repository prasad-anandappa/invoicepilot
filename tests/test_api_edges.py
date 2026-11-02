"""API hardening: validation, idempotency, output escaping."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

STRUCTURED = {
    "description": "Logo design",
    "client_name": "Idem Client",
    "amount": "120.00",
}


def test_structured_rejects_bad_money():
    bad_payloads = [
        {**STRUCTURED, "amount": "-5"},      # negative
        {**STRUCTURED, "amount": "0"},       # zero
        {**STRUCTURED, "amount": "99999999"},  # beyond the sane cap
        {**STRUCTURED, "tax_rate": "101"},   # tax over 100%
        {**STRUCTURED, "tax_rate": "-1"},    # negative tax
        {**STRUCTURED, "description": ""},   # empty description
        {**STRUCTURED, "client_name": ""},   # empty client
    ]
    for payload in bad_payloads:
        r = client.post("/api/invoices", json=payload)
        assert r.status_code == 422, payload


def test_from_text_rejects_unusable_input():
    for text in ["", "   ", "Free work for Maria, $0", "No money mentioned"]:
        r = client.post("/api/invoices/from-text", json={"text": text})
        assert r.status_code == 422, text


def test_structured_happy_path():
    r = client.post("/api/invoices", json=STRUCTURED)
    assert r.status_code == 200
    inv = r.json()["invoice"]
    assert inv["status"] == "SENT"
    assert inv["total"] == "120.00"
    assert inv["pay_link"].startswith("/mock-pay/")


def test_idempotency_key_deduplicates_structured():
    headers = {"Idempotency-Key": "test-key-structured-1"}
    r1 = client.post("/api/invoices", json=STRUCTURED, headers=headers)
    r2 = client.post("/api/invoices", json=STRUCTURED, headers=headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["invoice"]["id"] == r2.json()["invoice"]["id"]
    assert "Duplicate request ignored" in r2.json()["steps"][0]["summary"]

    # a different key is a genuinely new invoice
    r3 = client.post(
        "/api/invoices",
        json=STRUCTURED,
        headers={"Idempotency-Key": "test-key-structured-2"},
    )
    assert r3.json()["invoice"]["id"] != r1.json()["invoice"]["id"]


def test_idempotency_key_deduplicates_from_text():
    headers = {"Idempotency-Key": "test-key-text-1"}
    body = {"text": "Poster design for Idem Text Client, $60, due tomorrow"}
    r1 = client.post("/api/invoices/from-text", json=body, headers=headers)
    r2 = client.post("/api/invoices/from-text", json=body, headers=headers)
    assert r1.json()["invoice"]["id"] == r2.json()["invoice"]["id"]


def test_mock_checkout_escapes_user_html():
    payload = {
        "description": "Test <b>job</b>",
        "client_name": "<script>alert(1)</script>",
        "amount": "10.00",
    }
    r = client.post("/api/invoices", json=payload)
    assert r.status_code == 200
    link = r.json()["invoice"]["pay_link"]

    page = client.get(link)
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "&lt;b&gt;job&lt;/b&gt;" in page.text


def test_simulate_pay_twice_stays_paid():
    r = client.post(
        "/api/invoices/from-text",
        json={"text": "Double pay check for Dbl Client, $25, due tomorrow"},
    )
    inv_id = r.json()["invoice"]["id"]
    r1 = client.post(f"/api/invoices/{inv_id}/simulate-pay")
    r2 = client.post(f"/api/invoices/{inv_id}/simulate-pay")
    assert r1.status_code == r2.status_code == 200
    assert r2.json()["invoice"]["status"] == "PAID"


def test_unknown_things_404():
    assert client.post("/api/invoices/nope/simulate-pay").status_code == 404
    assert client.post("/api/invoices/nope/check-status").status_code == 404
    assert client.post("/api/invoices/nope/remind").status_code == 404
    assert client.get("/mock-pay/MOCK-ORDER-9999").status_code == 404
