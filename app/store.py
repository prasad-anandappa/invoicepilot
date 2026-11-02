"""In-memory invoice store (demo-grade; swap for a DB in production)."""
from __future__ import annotations

import itertools
import threading

from .models import Invoice


class InvoiceStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._invoices: dict[str, Invoice] = {}
        self._idempotency_keys: dict[str, str] = {}  # key -> invoice id

    def next_number(self) -> tuple[str, str]:
        n = next(self._seq)
        return f"inv-{n:04d}", f"INV-{1000 + n}"

    def add(self, invoice: Invoice) -> Invoice:
        with self._lock:
            self._invoices[invoice.id] = invoice
        return invoice

    def by_idempotency_key(self, key: str) -> Invoice | None:
        """Return the invoice a previous request with this key created."""
        invoice_id = self._idempotency_keys.get(key)
        return self._invoices.get(invoice_id) if invoice_id else None

    def remember_idempotency_key(self, key: str, invoice: Invoice) -> None:
        with self._lock:
            self._idempotency_keys[key] = invoice.id

    def get(self, invoice_id: str) -> Invoice:
        return self._invoices[invoice_id]

    def by_order(self, order_id: str) -> Invoice | None:
        for inv in self._invoices.values():
            if inv.paypal_order_id == order_id:
                return inv
        return None

    def all(self) -> list[Invoice]:
        return list(self._invoices.values())
