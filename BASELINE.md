# InvoicePilot Pro — Week-1 Baseline Freeze

**Date:** 2026-11-02
**Source:** fresh copy of `~/workspace/hackathons/paypal-project/` (github.com/prasad-anandappa/invoicepilot).
The source repo was **not** modified, committed to, or pushed — it is under live hackathon judging
(PayPal deadline Nov 12, Arbiter Dec 21). All work happens in this copy, which has its own local
git repo and no remotes.

## Test baseline (clean run in this copy)

```
$ .venv/bin/python -m pytest -q
62 passed, 1 warning in 0.83s
```

- **62 passed, 0 failed** — matches the expected count exactly.
- The 1 warning is a Starlette/httpx deprecation notice from the test client, pre-existing and unrelated.
- Test files: `tests/test_parser.py`, `tests/test_parser_edges.py`, `tests/test_invoice_math.py`,
  `tests/test_payments.py`, `tests/test_agent_flow.py`, `tests/test_api_edges.py`, `tests/test_llm_layer.py`.
- **Regression gate:** these 62 tests must pass **unchanged** at every milestone.
  New behaviour gets new tests; existing tests are never edited.

## API contract freeze

- OpenAPI schema exported from the current FastAPI app to
  [`docs/openapi-v1.json`](docs/openapi-v1.json) — this is the frozen v1 contract the
  Flutter app codes against. Any later change is a deliberate, reviewed diff.
- Endpoints (10 paths): `GET /`, `GET /api/health`, `GET|POST /api/invoices`,
  `POST /api/invoices/from-text`, `GET /api/invoices/{invoice_id}`,
  `POST /api/invoices/{invoice_id}/check-status`, `POST /api/invoices/{invoice_id}/remind`,
  `POST /api/invoices/{invoice_id}/simulate-pay`, `GET /api/reconcile`, `GET /mock-pay/{order_id}`.

## Environment

- Python 3.12.3, fresh venv in this copy.
- Baseline deps from `requirements.txt` (fastapi, uvicorn, httpx, pydantic, pytest).
- Week-1 additions: SQLAlchemy 2.1.6, Alembic 1.20.1, psycopg 3.3.6 (binary) — see `requirements.txt`.
- **No Postgres server is available on this machine** (no `psql`/`postgres` binaries).
  Per the execution pack's fallback, the DB layer is built on SQLAlchemy 2.0 with
  **SQLite as the dev/test dialect** and **Postgres as the production dialect**
  (same models, `DATABASE_URL` selects the engine; money is `Numeric`, payloads use
  JSON with a JSONB variant on Postgres, UUIDs stored as strings for portability).
