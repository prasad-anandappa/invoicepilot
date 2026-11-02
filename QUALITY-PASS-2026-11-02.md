# InvoicePilot — Quality Hardening Pass, 2026-11-02

**Order behind this pass:** "best projects, top quality code, ours should stand top and win prizes."
**Scope:** full code review of `app/`, test-suite strengthening, public-face polish (README / submission draft / repo hygiene). Demo directory untouched (separate lane).

## What was weak → what changed

### Correctness (the big one)
- **The Sandbox client never captured payments.** PayPal's Orders v2 lifecycle is CREATED → APPROVED → (merchant capture) → COMPLETED. The old client only created and read orders, so in live sandbox mode a buyer could approve and the money would never settle — while the demo (mock jumped straight to COMPLETED) hid the gap. **Fixed:** `capture_order()` on both clients; the mock now mirrors the real lifecycle (approve ≠ settled); `check_status` captures APPROVED orders and only then marks an invoice PAID. Sandbox capture treats PayPal's 422 already-captured response as a read-back, not a failure.
- **OAuth token was cached forever.** Sandbox tokens expire (~9h); long-running demos would start failing. **Fixed:** expiry tracked with a 60-second safety margin and automatic refresh.
- **Mock client raised bare `KeyError`** for unknown orders (would surface as a 500). **Fixed:** a domain `PayPalClientError`, and `check_status` degrades PayPal failures (domain error or HTTP error) into a reported, retryable agent step instead of crashing the loop.

### Input validation (there was effectively none)
- **Structured API accepted anything:** negative amounts, $0, tax rates of −1% or 101%, empty names. **Fixed:** Pydantic bounds (amount > 0 and ≤ $10M, tax 0–100, non-empty fields, text length caps) + domain invariants on the `Invoice` model itself, so no entry point can mint a bad invoice.
- **Natural-language parser:** `$0` invoices accepted → now rejected; tax > 100% → rejected with a friendly message; `"$450.999"` left a stray "9" in the description → amounts now parse fully and round HALF_UP; **"Feb 30" silently clamped to Feb 28** → impossible dates now resolve to "no due date" like other unparseable input.
- **LLM output was trusted blindly.** A hallucinated negative amount or 250% tax rate would have become an invoice. **Fixed:** every LLM field is validated (positive amount, tax 0–100, non-empty description, parseable date, length caps); anything off returns `None` and the deterministic parser takes over.
- **Config:** an invalid `PAYPAL_MODE` silently fell back to sandbox URLs → now fails fast with a clear error.

### Security basics
- **XSS on the mock checkout page:** client name and description were interpolated into HTML unescaped — a client named `<script>…` would execute. **Fixed:** all user-supplied values are `html.escape`d before rendering (covered by a regression test).
- Secrets: none in code before or after (env-only, `.env` gitignored); `.gitignore` also hardened to `.env.*` with an explicit `!.env.example` exception.

### Duplicate requests
- Double-clicks/retries created duplicate invoices (and duplicate PayPal orders). **Fixed:** both create endpoints honour an `Idempotency-Key` header — a replay returns the original invoice with an explicit "duplicate ignored" step. Without a key, repeat requests still create separate invoices (documented behaviour, covered by a test).

### Smaller polish
- Reminder copy no longer contains a dangling "pay securely here: " when an invoice has no payment link yet.
- README quickstart corrected (`cd paypal-project` → the real clone URL + folder) and **verified from a clean copy**: fresh venv → `pip install -r requirements.txt` → server boots, UI 200, full suite green.
- README + SUBMISSION-DRAFT updated for the capture lifecycle, the hardening story, and accurate test counts.

## Tests: 18 → 62, all passing

New files: `tests/test_parser_edges.py` (15), `tests/test_payments.py` (13), `tests/test_api_edges.py` (8), `tests/test_llm_layer.py` (8 incl. parametrised invalid-output cases).

Coverage added where it matters: payment state machine (CREATED → APPROVED → COMPLETED, capture-before-approval rejected, idempotent capture, `paid_at` stamped exactly once), reconcile boundaries (due-today is *not* overdue; the $535 / $450 / $85 demo scenario reproduced at agent level and again against a live server), parser hostility (empty input, $0, absurd tax, impossible dates, year roll-overs), API validation + idempotency replays, checkout-page escaping, and LLM-output validation with a stubbed transport.

Verified end-to-end after the changes, fresh server process: create $450 (Maria) → simulate pay → PAID; create $85 (Priya, due yesterday) → OVERDUE; reconcile = **$535 invoiced / $450 collected / $85 overdue**. Unchanged.

## Remaining honest gaps

1. **Live PayPal Sandbox still unexercised** — credentials are pending (the saved-login issue from earlier today). The sandbox client is code-complete (create / capture / get, token refresh) and the mock mirrors its lifecycle, but no real sandbox order has been placed yet. This is the single biggest remaining risk: PayPal field-level quirks (e.g. breakdown rules) can only be proven live.
2. **No return/cancel URLs** in the sandbox `application_context` — after approving on the real PayPal site, the buyer lands on a PayPal default page instead of back in the app. Needs a public URL decision (localhost won't do for a hosted demo).
3. **In-memory store** — invoices vanish on restart. Fine for judging; a SQLite swap is the obvious next step.
4. **Reminders are composed, not delivered** (mock inbox / recorded only). Real email delivery is on the roadmap in the README.
5. **No webhooks** — PAID detection is polling by design for the demo.

## Blunt verdict

For **Best Use of Agentic Commerce**, this is now a credible contender rather than a working demo. The story judges reward — an agent that runs an entire commerce loop unattended — is backed by the things that usually separate winners from the pile: a *correct* payment lifecycle (most hackathon entries never model capture), provably exact money math, idempotency, and a zero-setup quickstart that genuinely works. The architecture (tools behind one interface, mock ≡ sandbox behaviour) reads as engineered, not assembled.

What could still raise it, in order: **(1) exercise the live sandbox end-to-end** the moment credentials land and fix whatever PayPal says — a submission that can honestly claim "real sandbox orders, captured" beats one that claims "code-complete"; **(2) the demo video** (in progress in the parallel lane) — for Devpost, the video *is* the first impression; **(3) webhooks** for instant PAID detection, turning "poll and capture" into "reacts the second money moves"; **(4) SQLite persistence** so a judge can restart the server mid-demo without losing the books.
