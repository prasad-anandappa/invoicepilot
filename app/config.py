"""Configuration for InvoicePilot.

Everything is driven by environment variables so the same code runs in
deterministic MOCK mode (no credentials, used for the demo and tests) and in
real PayPal Sandbox mode (credentials dropped in later, no code changes).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_mode: str = "sandbox"  # sandbox | live
    paypal_webhook_id: str = ""  # enables webhook signature verification
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    @property
    def paypal_base_url(self) -> str:
        if self.paypal_mode == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def paypal_mock(self) -> bool:
        """Mock mode is the default whenever real credentials are absent."""
        return not (self.paypal_client_id and self.paypal_client_secret)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


def load_settings() -> Settings:
    mode = os.environ.get("PAYPAL_MODE", "sandbox").strip().lower()
    if mode not in ("sandbox", "live"):
        raise ValueError(
            f"PAYPAL_MODE must be 'sandbox' or 'live', got {mode!r}"
        )
    return Settings(
        paypal_client_id=os.environ.get("PAYPAL_CLIENT_ID", "").strip(),
        paypal_client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", "").strip(),
        paypal_mode=mode,
        paypal_webhook_id=os.environ.get("PAYPAL_WEBHOOK_ID", "").strip(),
        llm_api_key=os.environ.get("INVOICEPILOT_LLM_API_KEY", "").strip(),
        llm_base_url=os.environ.get(
            "INVOICEPILOT_LLM_BASE_URL", "https://api.openai.com/v1"
        ).strip(),
        llm_model=os.environ.get("INVOICEPILOT_LLM_MODEL", "gpt-4o-mini").strip(),
    )
