"""InvoicePilot — FastAPI app.

Run:  uvicorn app.main:app --reload --port 8000
Open: http://localhost:8000
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .agent import InvoicePilotAgent
from .config import load_settings
from .paypal_client import MockPayPalClient, build_paypal_client
from .store import InvoiceStore

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

settings = load_settings()
store = InvoiceStore()
paypal = build_paypal_client(settings)
agent = InvoicePilotAgent(settings, store, paypal)

app = FastAPI(title="InvoicePilot", version="1.0.0")


class FromTextRequest(BaseModel):
    text: str


class StructuredInvoiceRequest(BaseModel):
    description: str
    client_name: str
    amount: Decimal
    tax_rate: Decimal = Decimal("0")
    due_date: date | None = None


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "paypal_mode": paypal.name,
        "paypal_config": settings.paypal_mode,
        "llm": "api" if settings.llm_enabled else "rule-based fallback",
    }


@app.post("/api/invoices/from-text")
def invoice_from_text(req: FromTextRequest) -> dict:
    try:
        invoice, steps = agent.run_from_text(req.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"invoice": invoice.to_dict(), "steps": steps}


@app.post("/api/invoices")
def invoice_structured(req: StructuredInvoiceRequest) -> dict:
    invoice = agent.create_invoice_structured(
        description=req.description,
        client_name=req.client_name,
        amount=req.amount,
        tax_rate=req.tax_rate,
        due_date=req.due_date,
    )
    steps = agent.create_paypal_order(invoice)
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
    paypal.simulate_payment(invoice.paypal_order_id)
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
  <div class="amt">${invoice.total} {invoice.currency}</div>
  <p class="muted">{invoice.description}<br>Invoice {invoice.number} · for {invoice.client_name}</p>
  <button id="pay">Pay now</button>
  <p class="note">Order {order_id} · InvoicePilot demo</p>
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
