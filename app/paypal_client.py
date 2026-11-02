"""PayPal integration: real Sandbox REST client + deterministic mock.

The agent talks only to this interface. With PAYPAL_CLIENT_ID /
PAYPAL_CLIENT_SECRET set, orders are created against the PayPal Sandbox
Orders v2 API. Without them, MockPayPalClient simulates the exact same
response shapes deterministically, so the demo and tests run end-to-end
with no credentials and no real money.

Order lifecycle (both clients): CREATED -> APPROVED (buyer approved on
the PayPal checkout) -> COMPLETED (merchant captures). An order is only
money-in-hand once it is COMPLETED, so the agent captures APPROVED
orders before marking an invoice PAID.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .models import Invoice, fmt


class PayPalClientError(Exception):
    """A PayPal-layer failure the caller can surface cleanly (no 500s)."""


@dataclass
class PayPalOrder:
    order_id: str
    status: str  # CREATED | APPROVED | COMPLETED | VOIDED
    approve_link: str


class MockPayPalClient:
    """Deterministic in-memory stand-in for the PayPal Sandbox APIs."""

    name = "mock"

    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self._orders: dict[str, dict] = {}

    def _lookup(self, order_id: str) -> dict:
        try:
            return self._orders[order_id]
        except KeyError:
            raise PayPalClientError(f"unknown order {order_id}") from None

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
        order = self._lookup(order_id)
        return PayPalOrder(
            order_id=order_id,
            status=order["status"],
            approve_link=f"/mock-pay/{order_id}",
        )

    def simulate_payment(self, order_id: str) -> PayPalOrder:
        """Demo-only: the buyer approves on the mock checkout page.

        Mirrors the real flow: approving is not settling. The order sits
        at APPROVED until the agent captures it (see capture_order).
        """
        order = self._lookup(order_id)
        if order["status"] == "CREATED":
            order["status"] = "APPROVED"
        return self.get_order(order_id)

    def capture_order(self, order_id: str) -> PayPalOrder:
        order = self._lookup(order_id)
        if order["status"] == "COMPLETED":
            return self.get_order(order_id)  # capture is idempotent
        if order["status"] != "APPROVED":
            raise PayPalClientError(
                f"order {order_id} is {order['status']} and cannot be captured"
            )
        order["status"] = "COMPLETED"
        return self.get_order(order_id)


class SandboxPayPalClient:
    """PayPal Sandbox Orders v2 client (used when credentials exist)."""

    name = "sandbox"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        resp = httpx.post(
            f"{self._settings.paypal_base_url}/v1/oauth2/token",
            auth=(self._settings.paypal_client_id, self._settings.paypal_client_secret),
            data={"grant_type": "client_credentials"},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        # Refresh a minute early so a token never dies mid-flow.
        self._token_expires_at = time.monotonic() + max(
            60, int(data.get("expires_in", 32400)) - 60
        )
        return self._token

    def _headers(self, request_id: str | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
        }
        if request_id:
            # PayPal-Request-Id makes order creation idempotent at the
            # PayPal layer too: a retried create for the same invoice
            # returns the original order instead of minting a second one.
            headers["PayPal-Request-Id"] = request_id
        return headers

    def verify_webhook_signature(
        self, headers: dict, event: dict, webhook_id: str
    ) -> bool:
        """Ask PayPal to verify a webhook delivery's signature.

        Used by the webhook endpoint when PAYPAL_WEBHOOK_ID is set, so
        forged deliveries are rejected before they can touch an invoice.
        """
        payload = {
            "transmission_id": headers.get("paypal-transmission-id", ""),
            "transmission_time": headers.get("paypal-transmission-time", ""),
            "cert_url": headers.get("paypal-cert-url", ""),
            "auth_algo": headers.get("paypal-auth-algo", ""),
            "transmission_sig": headers.get("paypal-transmission-sig", ""),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        resp = httpx.post(
            f"{self._settings.paypal_base_url}/v1/notifications/"
            "verify-webhook-signature",
            headers=self._headers(),
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json().get("verification_status") == "SUCCESS"

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
            headers=self._headers(request_id=f"invoicepilot-{invoice.id}"),
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

    def capture_order(self, order_id: str) -> PayPalOrder:
        try:
            resp = httpx.post(
                f"{self._settings.paypal_base_url}/v2/checkout/orders/"
                f"{order_id}/capture",
                headers=self._headers(
                    request_id=f"invoicepilot-capture-{order_id}"
                ),
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Re-capturing an already-captured order is not a failure —
            # read the order back and report its real state.
            if exc.response.status_code == 422:
                return self.get_order(order_id)
            raise
        data = resp.json()
        link = next(
            (l["href"] for l in data.get("links", []) if l.get("rel") == "approve"),
            "",
        )
        return PayPalOrder(
            order_id=data["id"], status=data["status"], approve_link=link
        )

    def simulate_payment(self, order_id: str) -> PayPalOrder:
        raise RuntimeError(
            "simulate_payment is mock-only; in sandbox mode the buyer pays "
            "through the real PayPal approve link"
        )


def build_paypal_client(settings: Settings):
    if settings.paypal_mock:
        return MockPayPalClient()
    return SandboxPayPalClient(settings)
