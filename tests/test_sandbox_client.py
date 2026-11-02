"""Sandbox PayPal client hardening, exercised offline via stubbed httpx.

No network and no credentials: httpx.post/get in app.paypal_client are
replaced with recorders that return PayPal-shaped responses, so the
client's real request construction (headers, payload, token handling,
422-on-capture) is what gets asserted.
"""
from datetime import date
from decimal import Decimal

import httpx
import pytest

import app.paypal_client as pc
from app.config import Settings
from app.models import Invoice


def _invoice() -> Invoice:
    return Invoice(
        id="inv-0001",
        number="INV-1001",
        client_name="Maria",
        description="Logo design",
        subtotal=Decimal("450"),
        tax_rate=Decimal("0"),
        due_date=date(2026, 11, 9),
    )


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://sandbox.example")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("err", request=request, response=response)

    def json(self):
        return self._data


@pytest.fixture
def calls(monkeypatch):
    recorded = {"post": [], "get": [], "token_calls": 0}

    def fake_post(url, **kwargs):
        recorded["post"].append((url, kwargs))
        if url.endswith("/v1/oauth2/token"):
            recorded["token_calls"] += 1
            return _Resp({"access_token": "tok-1", "expires_in": 32400})
        if url.endswith("/capture"):
            return _Resp(
                {"id": "ORDER-1", "status": "COMPLETED", "links": []}
            )
        if url.endswith("/verify-webhook-signature"):
            return _Resp({"verification_status": "SUCCESS"})
        return _Resp(
            {
                "id": "ORDER-1",
                "status": "CREATED",
                "links": [
                    {"rel": "approve", "href": "https://paypal.example/approve"}
                ],
            }
        )

    def fake_get(url, **kwargs):
        recorded["get"].append((url, kwargs))
        return _Resp(
            {"id": "ORDER-1", "status": "COMPLETED", "links": []}
        )

    monkeypatch.setattr(pc.httpx, "post", fake_post)
    monkeypatch.setattr(pc.httpx, "get", fake_get)
    return recorded


def _client() -> pc.SandboxPayPalClient:
    return pc.SandboxPayPalClient(
        Settings(paypal_client_id="cid", paypal_client_secret="secret")
    )


def test_create_order_sends_request_id_and_breakdown(calls):
    order = _client().create_order(_invoice())
    assert order.order_id == "ORDER-1"
    assert order.approve_link == "https://paypal.example/approve"
    url, kwargs = calls["post"][-1]
    assert url.endswith("/v2/checkout/orders")
    assert kwargs["headers"]["PayPal-Request-Id"] == "invoicepilot-inv-0001"
    unit = kwargs["json"]["purchase_units"][0]
    assert unit["amount"]["value"] == "450.00"
    assert unit["amount"]["breakdown"]["item_total"]["value"] == "450.00"


def test_token_is_cached_across_calls(calls):
    client = _client()
    client.create_order(_invoice())
    client.create_order(_invoice())
    assert calls["token_calls"] == 1


def test_capture_sends_request_id(calls):
    order = _client().capture_order("ORDER-1")
    assert order.status == "COMPLETED"
    _url, kwargs = calls["post"][-1]
    assert kwargs["headers"]["PayPal-Request-Id"] == (
        "invoicepilot-capture-ORDER-1"
    )


def test_capture_422_reads_order_back(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        pc.httpx,
        "post",
        lambda url, **kw: (
            _Resp({"access_token": "t", "expires_in": 32400})
            if url.endswith("/token")
            else _Resp({}, status=422)
        ),
    )
    monkeypatch.setattr(
        pc.httpx,
        "get",
        lambda url, **kw: _Resp(
            {"id": "ORDER-1", "status": "COMPLETED", "links": []}
        ),
    )
    assert client.capture_order("ORDER-1").status == "COMPLETED"


def test_verify_webhook_signature_success(calls):
    ok = _client().verify_webhook_signature(
        {
            "paypal-transmission-id": "tid",
            "paypal-transmission-time": "2026-11-02T00:00:00Z",
            "paypal-cert-url": "https://paypal.example/cert",
            "paypal-auth-algo": "SHA256withRSA",
            "paypal-transmission-sig": "sig",
        },
        {"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"},
        "WHID-123",
    )
    assert ok is True
    url, kwargs = calls["post"][-1]
    assert url.endswith("/v1/notifications/verify-webhook-signature")
    assert kwargs["json"]["webhook_id"] == "WHID-123"
