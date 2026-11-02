"""Natural-language -> structured invoice parsing.

Two layers:
1. A deterministic rule-based parser (regex + date logic). This is the
   default and always works — no API key, no network, fully testable.
2. An optional LLM parser (see llm.py) that is tried first when a key is
   configured; any failure falls back to the rule-based parser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .models import money

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

MONTHS = {
    name.lower(): i
    for i, name in enumerate(
        [
            "January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}
MONTHS.update({name[:3].lower(): num for name, num in list(MONTHS.items())})


@dataclass
class ParsedInvoice:
    description: str
    client_name: str
    amount: Decimal
    tax_rate: Decimal = Decimal("0")
    currency: str = "USD"
    due_date: date | None = None  # None -> caller applies default


_AMOUNT_PATTERNS = [
    re.compile(r"\$\s?([\d,]+(?:\.\d+)?)"),
    re.compile(r"\bUSD\s?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\b([\d,]+(?:\.\d+)?)\s?(?:dollars|bucks|USD)\b", re.IGNORECASE),
]
_TAX_RE = re.compile(r"(?:plus\s+)?(\d+(?:\.\d+)?)\s?%\s*tax", re.IGNORECASE)
_IN_DAYS_RE = re.compile(r"\bin\s+(\d+)\s+days?\b", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(20\d{2}))?\b")
_MONTH_DAY_RE = re.compile(
    r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_WEEKDAY_RE = re.compile(
    r"\b(?:next\s+)?(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _next_weekday(today: date, weekday: int) -> date:
    delta = (weekday - today.weekday()) % 7
    if delta == 0:
        delta = 7  # "due Friday" said on a Friday means the coming Friday
    return today + timedelta(days=delta)


def _parse_due(text: str, today: date) -> tuple[date | None, str]:
    """Return (due_date, text with the due phrase removed)."""
    low = text.lower()

    m = _ISO_DATE_RE.search(text)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d, (text[: m.start()] + text[m.end():]).strip()
        except ValueError:
            pass

    m = _MONTH_DAY_RE.search(text)
    if m:
        month = MONTHS[m.group(1).lower()]
        day = int(m.group(2))
        try:
            d = date(today.year, month, day)
        except ValueError:
            d = None  # e.g. "Feb 30" — not a real date; keep looking
        if d is not None:
            if (today - d).days > 30:  # clearly meant the next occurrence
                d = date(today.year + 1, month, day)
            return d, (text[: m.start()] + text[m.end():]).strip()

    m = _SLASH_DATE_RE.search(text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(year, month, day)
            if not m.group(3) and (today - d).days > 30:
                d = date(year + 1, month, day)
            return d, (text[: m.start()] + text[m.end():]).strip()
        except ValueError:
            pass

    if "day after tomorrow" in low:
        return today + timedelta(days=2), re.sub(
            r"day after tomorrow", "", text, flags=re.IGNORECASE
        ).strip()
    if "tomorrow" in low:
        return today + timedelta(days=1), re.sub(
            r"tomorrow", "", text, flags=re.IGNORECASE
        ).strip()
    if "yesterday" in low:
        return today - timedelta(days=1), re.sub(
            r"yesterday", "", text, flags=re.IGNORECASE
        ).strip()
    if re.search(r"\btoday\b", low):
        return today, re.sub(r"today", "", text, flags=re.IGNORECASE).strip()

    m = _IN_DAYS_RE.search(text)
    if m:
        return today + timedelta(days=int(m.group(1))), (
            text[: m.start()] + text[m.end():]
        ).strip()

    m = _WEEKDAY_RE.search(low)
    if m and ("due" in low or "by" in low or "on" in low):
        return _next_weekday(today, WEEKDAYS[m.group(1).lower()]), (
            text[: m.start()] + text[m.end():]
        ).strip()

    return None, text


def parse_invoice_text(text: str, today: date | None = None) -> ParsedInvoice:
    """Parse a plain-language work description into invoice fields."""
    today = today or date.today()
    working = " ".join(text.strip().split())
    if not working:
        raise ValueError("Empty invoice description")

    # --- due date (strip the phrase first so it can't pollute other fields)
    due_date, working = _parse_due(working, today)
    working = re.sub(r"\bdue\b", "", working, flags=re.IGNORECASE)
    working = re.sub(r"\s+", " ", working).strip(" ,")

    # --- tax
    tax_rate = Decimal("0")
    m = _TAX_RE.search(working)
    if m:
        tax_rate = Decimal(m.group(1))
        if tax_rate > 100:
            raise ValueError(
                f"Tax rate of {tax_rate}% doesn't look right — "
                "it can't exceed 100%"
            )
        working = (working[: m.start()] + working[m.end():]).strip(" ,")

    # --- amount
    amount: Decimal | None = None
    for pattern in _AMOUNT_PATTERNS:
        m = pattern.search(working)
        if m:
            amount = money(m.group(1).replace(",", ""))
            working = (working[: m.start()] + working[m.end():]).strip(" ,")
            break
    if amount is None:
        raise ValueError(
            "No amount found — include one like $450 or 450 dollars"
        )
    if amount <= 0:
        raise ValueError("Invoice amount must be greater than zero")

    # --- description / client: "<work> for <client>"
    working = re.sub(r"\s+", " ", working).strip(" ,")
    description, client_name = working, "Client"
    m = re.search(r"\s+for\s+", working, flags=re.IGNORECASE)
    if m:
        description = working[: m.start()].strip(" ,")
        client_name = working[m.end():].strip(" ,") or "Client"
    if description:
        description = description[0].upper() + description[1:]

    return ParsedInvoice(
        description=description or "Services rendered",
        client_name=client_name,
        amount=amount,
        tax_rate=tax_rate,
        due_date=due_date,
    )
