"""InvoicePilot — FastAPI app.

Run:  uvicorn app.main:app --reload --port 8000
Open: http://localhost:8000
"""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .agent import InvoicePilotAgent
from .config import load_settings
from .paypal_client import MockPayPalClient, PayPalClientError, build_paypal_client
from .store import InvoiceStore

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

settings = load_settings()
store = InvoiceStore()
paypal = build_paypal_client(settings)
agent = InvoicePilotAgent(settings, store, paypal)

app = FastAPI(title="InvoicePilot", version="1.0.0")


class FromTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class StructuredInvoiceRequest(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    client_name: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, le=10_000_000)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    due_date: date | None = None


IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def _replay(key: str | None) -> dict | None:
    """If this Idempotency-Key was seen before, return the stored result.

    A retried or double-clicked create must never mint a second invoice
    (or a second PayPal order) for the same request.
    """
    if not key:
        return None
    existing = store.by_idempotency_key(key)
    if existing is None:
        return None
    return {
        "invoice": existing.to_dict(),
        "steps": [
            {
                "tool": "idempotency",
                "summary": (
                    f"Duplicate request ignored — {existing.number} "
                    "was already created for this key"
                ),
                "detail": "",
            }
        ],
    }


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "paypal_mode": paypal.name,
        "paypal_config": settings.paypal_mode,
        "llm": "api" if settings.llm_enabled else "rule-based fallback",
    }


@app.post("/api/invoices/from-text")
def invoice_from_text(
    req: FromTextRequest, idempotency_key: IdempotencyKey = None
) -> dict:
    replay = _replay(idempotency_key)
    if replay is not None:
        return replay
    try:
        invoice, steps = agent.run_from_text(req.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"PayPal request failed: {exc}"
        ) from exc
    if idempotency_key:
        store.remember_idempotency_key(idempotency_key, invoice)
    return {"invoice": invoice.to_dict(), "steps": steps}


@app.post("/api/invoices")
def invoice_structured(
    req: StructuredInvoiceRequest, idempotency_key: IdempotencyKey = None
) -> dict:
    replay = _replay(idempotency_key)
    if replay is not None:
        return replay
    try:
        invoice = agent.create_invoice_structured(
            description=req.description,
            client_name=req.client_name,
            amount=req.amount,
            tax_rate=req.tax_rate,
            due_date=req.due_date,
        )
        steps = agent.create_paypal_order(invoice)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"PayPal request failed: {exc}"
        ) from exc
    if idempotency_key:
        store.remember_idempotency_key(idempotency_key, invoice)
    return {"invoice": invoice.to_dict(), "steps": steps}


@app.get("/api/invoices")
def list_invoices() -> dict:
    return {"invoices": [i.to_dict() for i in store.all()]}


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str) -> dict:
    try:
        return {"invoice": store.get(invoice_id).to_dict()}
    except KeyError:
        raise HTTPException(status_code=404, detail="invoice not found") from None


@app.post("/api/invoices/{invoice_id}/check-status")
def check_status(invoice_id: str) -> dict:
    try:
        invoice = store.get(invoice_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="invoice not found") from None
    steps = agent.check_status(invoice)
    return {"invoice": invoice.to_dict(), "steps": steps}


@app.post("/api/invoices/{invoice_id}/remind")
def remind(invoice_id: str) -> dict:
    try:
        invoice = store.get(invoice_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="invoice not found") from None
    message, steps = agent.send_reminder(invoice)
    return {"invoice": invoice.to_dict(), "reminder": message, "steps": steps}


@app.post("/api/invoices/{invoice_id}/simulate-pay")
def simulate_pay(invoice_id: str) -> dict:
    """Mock-mode demo helper: the buyer completes payment."""
    if not isinstance(paypal, MockPayPalClient):
        raise HTTPException(
            status_code=400,
            detail="simulate-pay is only available in mock mode",
        )
    try:
        invoice = store.get(invoice_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="invoice not found") from None
    if not invoice.paypal_order_id:
        raise HTTPException(status_code=400, detail="invoice has no PayPal order")
    try:
        paypal.simulate_payment(invoice.paypal_order_id)
    except PayPalClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    steps = agent.check_status(invoice)
    return {"invoice": invoice.to_dict(), "steps": steps}


@app.get("/api/reconcile")
def reconcile() -> dict:
    return agent.reconcile()


@app.get("/mock-pay/{order_id}", response_class=HTMLResponse)
def mock_pay_page(order_id: str) -> str:
    """A tiny simulated PayPal checkout so the demo loop is clickable."""
    invoice = store.by_order(order_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="order not found")
    # Everything user-supplied is escaped before it lands in HTML.
    esc = html.escape
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PayPal (simulated checkout)</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#f5f7fa; margin:0;
         display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ background:#fff; border-radius:16px; box-shadow:0 10px 40px rgba(0,0,0,.12);
          padding:32px; width:min(420px, 92vw); text-align:center; }}
  .brand {{ color:#003087; font-weight:800; font-size:28px; font-style:italic; }}
  .brand span {{ color:#0070ba; }}
  .amt {{ font-size:34px; font-weight:700; margin:14px 0 4px; }}
  .muted {{ color:#5b6b7c; font-size:14px; }}
  button {{ background:#0070ba; color:#fff; border:0; border-radius:999px;
           padding:14px 22px; font-size:16px; font-weight:600; cursor:pointer;
           width:100%; margin-top:22px; }}
  button:hover {{ background:#005ea6; }}
  .note {{ margin-top:16px; font-size:12px; color:#8a97a5; }}
</style></head><body>
<div class="card">
  <div class="brand">Pay<span>Pal</span></div>
  <p class="muted">Simulated sandbox checkout — no real money moves</p>
  <div class="amt">${invoice.total} {esc(invoice.currency)}</div>
  <p class="muted">{esc(invoice.description)}<br>Invoice {esc(invoice.number)} · for {esc(invoice.client_name)}</p>
  <button id="pay">Pay now</button>
  <p class="note">Order {esc(order_id)} · InvoicePilot demo</p>
</div>
<script>
document.getElementById('pay').addEventListener('click', async () => {{
  const r = await fetch('/api/invoices/{invoice.id}/simulate-pay', {{method:'POST'}});
  if (r.ok) {{ window.location.href = '/?paid={invoice.number}'; }}
  else {{ alert('Payment failed in mock checkout'); }}
}});
</script></body></html>"""


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
