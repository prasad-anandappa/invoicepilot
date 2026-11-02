# Devpost Submission Draft — InvoicePilot

**Project name:** InvoicePilot
**Tagline:** The autonomous agent that invoices, collects and reconciles — you just do the work.

---

## Inspiration

Every freelancer knows the second, unpaid job: the admin of getting paid. You finish the work, then you still have to write the invoice, generate a payment link, check your account three days later, and draft that awkward *"just following up…"* email. Invoices get forgotten; late payments pile up; cash flow suffers. We asked: what if the entire get-paid loop was simply *handled* — by an agent you can walk away from?

## What it does

InvoicePilot is an autonomous AI agent for freelancers and small sellers:

1. **Describe the work in plain language** — *"Logo design for Maria's bakery, $450, due Friday."*
2. The agent **parses the note into a structured invoice** (client, amount, tax, real due date) with exact money math.
3. The agent **creates a PayPal order and sends the buyer a payment link** (PayPal Sandbox Orders v2).
4. The agent **monitors payment status, captures approved payments itself**, and marks the invoice PAID only when PayPal reports the capture COMPLETED — an approval alone never counts as money in.
5. If the due date passes unpaid, the agent **sends a polite, context-aware reminder** with the payment link.
6. At any moment, the agent **reconciles**: invoiced vs collected vs outstanding vs overdue.

The web UI makes the agent's work visible: a live step-by-step activity feed, invoice cards with status timelines, and a reconciliation panel.

## How we built it

- **Agent core (Python):** five explicit tool functions — `create_invoice`, `create_paypal_order`, `check_status`, `send_reminder`, `reconcile` — orchestrated by a small agent loop, each returning human-readable steps the UI renders live.
- **Understanding layer:** LLM tool-use behind an interface for natural-language extraction — with a **deterministic rule-based parser as a first-class fallback** (regex + calendar logic that resolves "due Friday", "in 3 days", "Nov 20", tax rates). The demo never depends on an API key being present.
- **Payments:** a PayPal client with two interchangeable implementations — the real **Sandbox REST client** (OAuth2 client-credentials with an auto-refreshing token + Orders v2 create/capture/get with itemised tax breakdown) and a **deterministic mock** that mirrors the exact same lifecycle (CREATED → APPROVED → captured → COMPLETED), including a clickable mock checkout page. Credentials switch modes; code doesn't change.
- **Money correctness:** `Decimal` arithmetic with ROUND_HALF_UP cent quantisation throughout — totals are exact and unit-tested.
- **Hardened like a payments product, not a demo:** idempotent invoice creation (`Idempotency-Key` — a retried request can never mint a duplicate invoice or a second PayPal order), validation at every boundary (API payloads, natural-language input, and untrusted LLM JSON, which is field-checked before it can become an invoice), HTML-escaped rendering of user text, and status checks that degrade to a reported step instead of crashing when PayPal is unreachable.
- **Web:** FastAPI backend + a single self-contained HTML/CSS/JS page (no build step), designed to show the agent working: activity feed, status pills, per-invoice timelines, reminder previews, reconciliation stats.
- **Tests:** 62 pytest tests — parser and its edge cases, invoice math, the full payment state machine, API validation and idempotency, LLM-output validation, and the complete get-paid loop over HTTP in mock mode. All run offline.

## Challenges we ran into

- **Natural dates are slippery.** "Due Friday" said *on* a Friday means next week; "Nov 20" said in December means next year; "Feb 30" isn't a date at all and must not silently become Feb 28. The parser resolves these against a reference date with explicit rules, all unit-tested.
- **"Approved" is not "paid".** PayPal's Orders flow separates buyer approval from merchant capture — an agent that marks invoices paid at approval is lying about money. InvoicePilot models the full state machine and captures before it celebrates, in the sandbox client and the mock alike.
- **Agents that touch money get retried and double-clicked.** Duplicate submissions and flaky networks are the norm, so creation is idempotent and every external failure surfaces as a visible, retryable step.
- **Demo-ability vs real APIs.** A judge must be able to run everything with zero setup, yet the integration must be genuinely real. The mock-behind-config design solved both: identical agent code paths, deterministic offline demo, real sandbox orders the moment credentials exist.
- **Polite persistence.** Reminder copy has to be firm about facts (amount, days overdue, link) and warm in tone — the composer builds messages from invoice state rather than using one fixed template.

## Accomplishments we're proud of

- A genuinely **end-to-end autonomous loop** — note → invoice → PayPal link → approval → capture → receipt → reconciliation — that a judge can run in two minutes.
- **Agentic commerce, not a chatbot:** the agent acts across tools and time (creates, sends, waits, checks, captures, chases), which is exactly the "walk-away agent" pattern.
- Money math that is provably exact, a payment lifecycle that is honestly modelled, and a fallback parser that makes the AI layer an enhancement, never a dependency.
- A clean-clone quickstart that actually works: three commands, zero credentials, 62 passing tests.

## What we learned

Designing the tool boundary is the product: once the five tools were crisp (inputs, outputs, human-readable steps), the agent behaviour, the UI, and the tests all fell out naturally. We also learned that trust in an agent that touches money comes from *visibility* — showing every step and every total, exactly.

## What's next

- PayPal webhooks for instant payment detection (replacing polling) and the Invoicing API for native PayPal invoices
- Real reminder delivery (email/SMS) with a gentle→firm escalation ladder
- Recurring invoices and multi-currency support
- Persistent storage and a small-business dashboard (aging report, cash-flow forecast)

## Built with

Python · FastAPI · PayPal Sandbox REST API (Orders v2, OAuth2) · LLM tool-use (OpenAI-compatible) · rule-based NLP parser · Decimal money engine · vanilla HTML/CSS/JS · pytest

## Try it

Repository includes a 3-command quickstart; it runs in deterministic mock mode with no credentials, and switches to the real PayPal Sandbox by setting two environment variables.
