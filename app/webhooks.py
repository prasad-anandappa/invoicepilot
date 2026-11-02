"""PayPal webhook handling.

Polling (``check_status``) works, but a webhook lets the agent react the
second money moves instead of the next time someone asks. This processor
handles the two events that matter for the get-paid loop:

- ``CHECKOUT.ORDER.APPROVED`` — the buyer approved; the agent captures
  (via the normal ``check_status`` path, so webhook and polling can
  never diverge) and the invoice flips to PAID on COMPLETED.
- ``PAYMENT.CAPTURE.COMPLETED`` — money settled; mark PAID.
- ``PAYMENT.CAPTURE.DENIED`` / ``CHECKOUT.ORDER.VOIDED`` — recorded on
  the invoice timeline so the freelancer sees it; no state is hidden.

PayPal delivers webhooks at-least-once, so event ids are deduplicated:
a redelivery is acknowledged and ignored, never double-processed.

Signature verification lives in the API layer (``app/main.py``): when a
webhook id is configured for a real (non-mock) client, deliveries are
verified with PayPal before they reach this processor. In demo/mock
mode there is nothing to verify against, and the endpoint says so in
its response instead of pretending.
"""
from __future__ import annotations

from .agent import InvoicePilotAgent
from .store import InvoiceStore

_HANDLED = {
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DENIED",
    "CHECKOUT.ORDER.VOIDED",
}


def _step(tool: str, summary: str, detail: str = "") -> dict:
    return {"tool": tool, "summary": summary, "detail": detail}


def _order_id_from(event: dict) -> str | None:
    """Pull the PayPal order id out of the event shapes PayPal sends."""
    resource = event.get("resource") or {}
    if event.get("event_type") == "CHECKOUT.ORDER.APPROVED":
        return resource.get("id")
    related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    if related.get("order_id"):
        return related["order_id"]
    # Capture events: the resource id is the capture id, not the order.
    # Fall back to any link-free identifier we can match, else None.
    return None


class PayPalWebhookProcessor:
    def __init__(self, agent: InvoicePilotAgent, store: InvoiceStore) -> None:
        self.agent = agent
        self.store = store
        self._processed: set[str] = set()

    def handle(self, event: dict) -> dict:
        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("event_type") or "").strip()
        if not event_id or not event_type:
            raise ValueError("webhook event must carry 'id' and 'event_type'")
        if event_id in self._processed:
            return {
                "received": True,
                "duplicate": True,
                "event_type": event_type,
                "invoice": None,
                "steps": [
                    _step(
                        "webhook",
                        f"Duplicate delivery of {event_id} ignored",
                        "PayPal webhooks are at-least-once; already processed",
                    )
                ],
            }
        self._processed.add(event_id)

        if event_type not in _HANDLED:
            return {
                "received": True,
                "duplicate": False,
                "event_type": event_type,
                "invoice": None,
                "steps": [
                    _step(
                        "webhook",
                        f"Event {event_type} acknowledged (no action needed)",
                    )
                ],
            }

        order_id = _order_id_from(event)
        invoice = self.store.by_order(order_id) if order_id else None
        if invoice is None:
            return {
                "received": True,
                "duplicate": False,
                "event_type": event_type,
                "invoice": None,
                "steps": [
                    _step(
                        "webhook",
                        f"Event {event_type} for unknown order {order_id or '?'}",
                        "No matching invoice — acknowledged and recorded",
                    )
                ],
            }

        if event_type in ("PAYMENT.CAPTURE.DENIED", "CHECKOUT.ORDER.VOIDED"):
            invoice.add_event(
                "note",
                f"PayPal webhook: {event_type} for order {order_id}",
            )
            return {
                "received": True,
                "duplicate": False,
                "event_type": event_type,
                "invoice": invoice.to_dict(),
                "steps": [
                    _step(
                        "webhook",
                        f"{invoice.number}: PayPal reported {event_type}",
                        "Noted on the invoice timeline; a fresh payment "
                        "link may be needed",
                    )
                ],
            }

        # APPROVED -> capture; CAPTURE.COMPLETED -> confirm. Both go
        # through the agent's normal status path so the state machine
        # (capture before PAID, paid_at stamped once) is identical
        # whether the trigger was a webhook or a poll.
        steps = [
            _step(
                "webhook",
                f"PayPal webhook {event_type} for {invoice.number}",
                f"Order {order_id}",
            )
        ]
        steps += self.agent.check_status(invoice)
        return {
            "received": True,
            "duplicate": False,
            "event_type": event_type,
            "invoice": invoice.to_dict(),
            "steps": steps,
        }
