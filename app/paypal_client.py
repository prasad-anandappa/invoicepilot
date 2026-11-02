"""PayPal integration: real Sandbox REST client + deterministic mock.

The agent talks only to this interface. With PAYPAL_CLIENT_ID /
PAYPAL_CLIENT_SECRET set, orders are created against the PayPal Sandbox
Orders v2 API. Without them, MockPayPalClient simulates the exact same
response shapes deterministically, so the demo and tests run end-to-end
with no credentials and no real money.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import httpx

from .config import Settings
from .models import Invoice, fmt


@dataclass
class PayPalOrder:
    order_id: str
    status: str  # CREATED | APPROVED | COMPLETED
    approve_link: str


class MockPayPalClient:
    """Deterministic in-memory stand-in for the PayPal Sandbox APIs."""

    name = "mock"

    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self._orders: dict[str, dict] = {}

    def create_order(self, invoice: Invoice) -> PayPalOrder:
        n = next(self._seq)
        order_id = f"MOCK-ORDER-{n:04d}"
        link = f"/mock-pay/{order_id}"  # in-app simulated PayPal checkout
        self._orders[order_id] = {
            "invoice_id": invoice.id,
            "status": "CREATED",
            "amount": fmt(invoice.total),
            "currency": invoice.currency,
        }
        return PayPalOrder(order_id=order_id, status="CREATED", approve_link=link)

    def get_order(self, order_id: str) -> PayPalOrder:
        order = self._orders[order_id]
        return PayPalOrder(
            order_id=order_id,
            status=order["status"],
            approve_link=f"/mock-pay/{order_id}",
        )

    def simulate_payment(self, order_id: str) -> PayPalOrder:
        """Demo-only: the buyer 'pays' on the mock checkout page."""
        order = self._orders[order_id]
        order["status"] = "COMPLETED"
        return self.get_order(order_id)


class SandboxPayPalClient:
    """PayPal Sandbox Orders v2 client (used when credentials exist)."""

    name = "sandbox"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None

    def _access_token(self) -> str:
        if self._token:
            return self._token
        resp = httpx.post(
            f"{self._settings.paypal_base_url}/v1/oauth2/token",
            auth=(self._settings.paypal_client_id, self._settings.paypal_client_secret),
            data={"grant_type": "client_credentials"},
            timeout=30.0,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
        }

    def create_order(self, invoice: Invoice) -> PayPalOrder:
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": invoice.number,
                    "description": invoice.description[:127],
                    "amount": {
                        "currency_code": invoice.currency,
                        "value": fmt(invoice.total),
                        "breakdown": {
                            "item_total": {
                                "currency_code": invoice.currency,
                                "value": fmt(invoice.subtotal),
                            },
                            "tax_total": {
                                "currency_code": invoice.currency,
                                "value": fmt(invoice.tax_amount),
                            },
                        },
                    },
                }
            ],
            "application_context": {
                "brand_name": "InvoicePilot",
                "user_action": "PAY_NOW",
            },
        }
        resp = httpx.post(
            f"{self._settings.paypal_base_url}/v2/checkout/orders",
            headers=self._headers(),
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        link = next(
            (l["href"] for l in data.get("links", []) if l.get("rel") == "approve"),
            "",
        )
        return PayPalOrder(order_id=data["id"], status=data["status"], approve_link=link)

    def get_order(self, order_id: str) -> PayPalOrder:
        resp = httpx.get(
            f"{self._settings.paypal_base_url}/v2/checkout/orders/{order_id}",
            headers=self._headers(),
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        link = next(
            (l["href"] for l in data.get("links", []) if l.get("rel") == "approve"),
            "",
        )
        return PayPalOrder(order_id=data["id"], status=data["status"], approve_link=link)

    def simulate_payment(self, order_id: str) -> PayPalOrder:
        raise RuntimeError(
            "simulate_payment is mock-only; in sandbox mode the buyer pays "
            "through the real PayPal approve link"
        )


def build_paypal_client(settings: Settings):
    if settings.paypal_mock:
        return MockPayPalClient()
    return SandboxPayPalClient(settings)
