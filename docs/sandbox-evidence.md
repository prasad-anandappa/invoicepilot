# Live PayPal Sandbox run — verifiable evidence

On **Nov 2, 2026 (Phoenix) / Nov 3, 2026 (UTC)** InvoicePilot ran one
complete, real transaction against the **PayPal Sandbox** (Orders v2) —
not the mock. The invoice below was created by the agent on a
Sandbox-configured instance, the buyer approved on PayPal's sandbox
checkout, the agent captured the payment itself, and
signature-verified webhooks closed the loop.

## The transaction

| Field | Value |
|---|---|
| Invoice | INV-1001 — "Sandbox end-to-end verification" for client "Sandbox Demo" |
| Amount | **$125.00 USD** |
| PayPal order ID | **`1B345558HU838203W`** |
| Order created | 2026-11-03T00:43:08Z |
| Order status | **COMPLETED** |
| PayPal capture ID | **`9F334151MX513935W`** |
| Capture status | **COMPLETED** — $125.00 USD |
| Capture time | 2026-11-03T00:47:05Z |
| Invoice state after the run | **PAID**, paid_at stamped 2026-11-03T00:47:05Z |

## Webhooks (signature verification enabled)

The instance ran with a configured `PAYPAL_WEBHOOK_ID`, so every
delivery was signature-verified before processing. PayPal delivered
the event sequence three times to the instance's webhook endpoint
(server log, all HTTP 200):

- 2026-11-03T00:47:05Z — first delivery: `CHECKOUT.ORDER.APPROVED`
  had already triggered the agent's capture; the capture-completed
  event flipped the invoice to PAID.
- 2026-11-03T00:47:18Z — duplicate delivery: acknowledged, ignored.
- 2026-11-03T00:55:35Z — duplicate delivery: acknowledged, ignored.

Duplicate handling is deliberate: webhook event IDs are deduplicated
and `paid_at` is stamped exactly once — the invoice record still
shows the single 00:47:05Z stamp, not either of the later times.

## How this was verified

- The invoice record (order ID, timeline, paid_at) is read from the
  Sandbox instance that ran the transaction.
- The order and capture were **re-read from the PayPal Sandbox API**
  (`GET /v2/checkout/orders/1B345558HU838203W`) on 2026-11-10: order
  status COMPLETED, one capture `9F334151MX513935W`, COMPLETED,
  $125.00 USD — matching the instance's records exactly.
- Anyone with a PayPal developer account can repeat the API read
  with the order ID above; sandbox order/capture IDs are safe to
  publish (no real money, no credentials involved).

## Why the lifecycle matters

PayPal separates buyer approval from merchant capture. Approving is
not settling: an agent that marks an invoice paid at approval is
reporting money it does not have. InvoicePilot models the full state
machine — CREATED → APPROVED → captured → COMPLETED — in the sandbox
client and the mock alike, and only the COMPLETED state counts as
paid. This run is the proof against the real API.
