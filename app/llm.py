"""Optional LLM parsing layer.

InvoicePilot never hard-requires an LLM: when INVOICEPILOT_LLM_API_KEY is
set, an OpenAI-compatible chat endpoint is asked to return the invoice
fields as JSON; on ANY failure (network, shape, timeout) the caller falls
back to the deterministic rule-based parser in parser.py.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from .config import Settings
from .parser import ParsedInvoice

_SYSTEM = (
    "You extract invoice fields from a freelancer's plain-language note. "
    "Reply with ONLY a JSON object: "
    '{"description": str, "client_name": str, "amount": number, '
    '"tax_rate_percent": number, "due_date": "YYYY-MM-DD" or null}. '
    "amount is the pre-tax subtotal. If no tax is mentioned, tax_rate_percent is 0."
)


def parse_with_llm(text: str, settings: Settings) -> ParsedInvoice | None:
    if not settings.llm_enabled:
        return None
    try:
        resp = httpx.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": f"Today is {date.today().isoformat()}.\nNote: {text}",
                    },
                ],
                "temperature": 0,
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        due = data.get("due_date")
        return ParsedInvoice(
            description=str(data["description"]),
            client_name=str(data.get("client_name") or "Client"),
            amount=Decimal(str(data["amount"])),
            tax_rate=Decimal(str(data.get("tax_rate_percent") or 0)),
            due_date=date.fromisoformat(due) if due else None,
        )
    except (httpx.HTTPError, KeyError, ValueError, InvalidOperation, TypeError):
        return None
