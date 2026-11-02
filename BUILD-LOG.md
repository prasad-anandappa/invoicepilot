# InvoicePilot — Build Log

**Date:** 2026-11-02 · **Builder:** Muse (delegated build lane) · **Author:** Prasad Dimba Anandappa
**Context:** PayPal AI Hackathon (Devpost), submissions close Nov 12, 2026 12:00 PM PT. Target category angle: Best Use of Agentic Commerce.

## What was built

| Piece | Path | Notes |
|---|---|---|
| Agent core | `app/agent.py` | 5 tools: create_invoice, create_paypal_order, check_status, send_reminder, reconcile; every call emits UI-readable steps |
| NL parser | `app/parser.py` | Deterministic: amounts ($/USD/dollars), tax %, due dates (weekday names, "in N days", tomorrow/yesterday, month names, ISO, M/D) |
| LLM layer | `app/llm.py` | Optional OpenAI-compatible extraction; any failure → parser fallback; never required |
| PayPal client | `app/paypal_client.py` | `SandboxPayPalClient` (OAuth2 + Orders v2, itemised tax breakdown) + `MockPayPalClient` (deterministic, default without credentials) |
| Money/models | `app/models.py` | Decimal, ROUND_HALF_UP cents; OVERDUE derived from due date, never stored |
| API + UI server | `app/main.py` | FastAPI; REST endpoints; simulated PayPal checkout page at `/mock-pay/{order_id}` |
| Web UI | `static/index.html` | One self-contained page: compose box + example chips, live agent activity feed, invoice cards with timelines + reminder preview, reconciliation stats |
| Config | `app/config.py`, `.env.example` | PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / PAYPAL_MODE; INVOICEPILOT_LLM_* |
| Tests | `tests/` | parser (8), invoice math (5), full agent flow over HTTP (5) |
| Docs | `README.md`, `SUBMISSION-DRAFT.md`, `LICENSE` (MIT) | Pitch, mermaid architecture, quickstart, demo script, Devpost text |

## Verification — tests

```
.venv/bin/python -m pytest -q
18 passed in 3.20s   (Python 3.12.3, venv inside the project)
```

## Verification — live mock flow (uvicorn on :8123, real curl calls)

1. **Health** → `{"ok":true,"paypal_mode":"mock","paypal_config":"sandbox","llm":"rule-based fallback"}`
2. **Create from plain language** — `POST /api/invoices/from-text` with
   *"Logo design for Maria's bakery, $450, due Friday"* →
   `INV-1001 · Maria's bakery · total 450.00 · status SENT · due 2026-11-06 (the coming Friday) · order MOCK-ORDER-0001 · pay_link /mock-pay/MOCK-ORDER-0001`
   Agent steps: create_invoice (×2) → create_paypal_order → check_status (CREATED).
3. **Buyer pays** — `POST /api/invoices/inv-0001/simulate-pay` → `status PAID, paid_at set`.
4. **Overdue path** — *"Birthday cake order for Priya, $85, due yesterday"* → `INV-1002 · status OVERDUE · total 85.00`.
5. **Reminder** — `POST /api/invoices/inv-0002/remind` →
   *"Hi Priya, a friendly nudge: invoice INV-1002 for $85.00 (Birthday cake order) was due 2026-11-01 and is now 1 day overdue. You can pay securely here: /mock-pay/MOCK-ORDER-0002 — it takes under a minute…"*
6. **Reconcile** — `GET /api/reconcile` →
   `2 invoices · invoiced 535.00 · collected 450.00 · outstanding 85.00 · overdue 85.00` with per-invoice paid/overdue lists and a plain-English summary.
7. **Pages** — mock checkout page `GET /mock-pay/MOCK-ORDER-0002` → HTTP 200; UI `GET /` → HTTP 200.

## Blocked / pending on the parent

- **Real PayPal Sandbox credentials** (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET from a developer.paypal.com sandbox app): the sandbox client code is written and mode-switches automatically when these env vars exist, but it could not be exercised without credentials. Mock mode is the default and is fully verified.
- **GitHub repo creation + push** — parent's step (repo must be public, with the MIT LICENSE included here).
- **Demo video (<3 min)** — parent's step; the README demo walkthrough is scripted for it.
- **Devpost submission itself** — parent's step; text is ready in SUBMISSION-DRAFT.md.
- Minor: in mock mode the reminder message embeds the relative mock pay path (`/mock-pay/…`); with real sandbox credentials the reminder automatically carries the genuine PayPal approve URL instead.
