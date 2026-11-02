"""LLM parsing layer: validated output or clean fallback to None.

The LLM's JSON is untrusted input — a hallucinated negative amount or
a 250% tax rate must never become an invoice; parse_with_llm returns
None and the caller falls back to the rule-based parser.
"""
import json

import pytest

from app.config import Settings
from app.llm import parse_with_llm


class FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def llm_settings():
    return Settings(llm_api_key="test-key")


def stub_post(monkeypatch, payload) -> None:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(
        "app.llm.httpx.post", lambda *a, **k: FakeResponse(content)
    )


def test_no_key_never_calls_out(llm_settings):
    assert parse_with_llm("anything", Settings()) is None


def test_valid_llm_output_parses(monkeypatch, llm_settings):
    stub_post(
        monkeypatch,
        {
            "description": "Logo design",
            "client_name": "Maria's bakery",
            "amount": 450,
            "tax_rate_percent": 0,
            "due_date": "2026-11-06",
        },
    )
    p = parse_with_llm("note", llm_settings)
    assert p is not None
    assert p.client_name == "Maria's bakery"
    assert p.amount == 450
    assert p.due_date.isoformat() == "2026-11-06"


def test_garbage_output_falls_back(monkeypatch, llm_settings):
    stub_post(monkeypatch, "I could not parse that, sorry!")
    assert parse_with_llm("note", llm_settings) is None


@pytest.mark.parametrize(
    "payload",
    [
        # negative amount
        {"description": "X", "client_name": "Y", "amount": -5,
         "tax_rate_percent": 0, "due_date": None},
        # absurd tax rate
        {"description": "X", "client_name": "Y", "amount": 10,
         "tax_rate_percent": 250, "due_date": None},
        # blank description
        {"description": "   ", "client_name": "Y", "amount": 10,
         "tax_rate_percent": 0, "due_date": None},
        # unparseable due date
        {"description": "X", "client_name": "Y", "amount": 10,
         "tax_rate_percent": 0, "due_date": "next Friday"},
        # missing amount entirely
        {"description": "X", "client_name": "Y",
         "tax_rate_percent": 0, "due_date": None},
    ],
)
def test_invalid_llm_output_falls_back(monkeypatch, llm_settings, payload):
    stub_post(monkeypatch, payload)
    assert parse_with_llm("note", llm_settings) is None
