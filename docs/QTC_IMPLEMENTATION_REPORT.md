# Quote-to-Cash: implementation report

Date: 2026-11-20 · Branch: `docs-production-build` (uncommitted at the time of writing)

## Status by phase

| Phase | Scope | Status |
|---|---|---|
| **A** | Customer CRM, service catalogue, rate cards (price lists), audit, numbering | **Done and tested** (SQLite and PostgreSQL) |
| B | Quotation engine, approval, Thai PDF / DOCX | Not started |
| C | Sales orders, invoices, payment tracking | Not started |
| D | AI agents (SalesIntake, Quotation, Finance, Collection) and Mira | Not started |
| E | n8n workflows, QTC dashboard, end-to-end tests | Not started |

Nothing beyond Phase A is claimed. The `/qtc` overview lists phases B–E as "Planned" text, not as buttons.

## Phase A: what exists

### Database (migrations `0007`, `0008`)

| Table | Purpose | Integrity rules in the database |
|---|---|---|
| `customers` | CRM record: code, names, company / individual, Thai tax ID + branch, contact details, payment terms, status | unique `(organization_id, code)`; unique `(organization_id, idempotency_key)`; checks on type and status |
| `customer_contacts` | People at the customer, main contact, **channel consent** (email, LINE) for later reminders | FK to customer (cascade) |
| `service_catalog` | What the organization sells; matching keywords (never prices) | unique `(organization_id, code)` |
| `price_lists` | Rate cards: currency, VAT rate, prices including or excluding VAT, validity, draft / active / archived, who approved | unique name per org; `vat_rate` in [0, 1); **one default per organization** (partial unique index) |
| `price_list_items` | Unit price of a service in a price list | unique `(price_list_id, service_id)`; `unit_price >= 0` |
| `qtc_number_sequences` | Per-organization counters (customer codes now; quotation / order / invoice numbers later) | unique `(organization_id, kind)` |
| `qtc_audit_events` | Append-only history: entity, action, actor, before / after | indexed by entity |

Every table carries `organization_id`. Money is `NUMERIC(18,2)` (amounts) or `NUMERIC(18,4)` (unit prices); rates
`NUMERIC(7,4)`; quantities `NUMERIC(14,3)`.

### Money rules (`app/qtc/money.py`)

- `Decimal` only; `to_decimal` **refuses floats** (API amounts are strings).
- Rounding: satang, `ROUND_HALF_UP`, **per line**; document totals are sums of rounded lines, so documents always
  add up as printed.
- VAT added on top, or extracted from VAT-inclusive prices; VAT-exempt lines; percent and fixed discounts.
- Thai 13-digit tax ID check digit.
- Used by Phase B for quotation lines; already covered by tests (VAT, rounding, discounts, invalid input).

### Numbering (`app/qtc/numbering.py`)

One atomic `UPDATE … SET next_value = next_value + 1 … RETURNING` per number, so concurrent requests never get the
same number, on PostgreSQL and SQLite. Found by testing: the first version ("highest existing code + 1" with retries)
returned 409 for some of 12 parallel requests on PostgreSQL. A code typed by hand that equals the next generated one
is skipped.

### REST API (`/v1/qtc`, `app/qtc/api.py`)

| Endpoint | Who |
|---|---|
| `GET /customers` (search, status, pagination), `GET /customers/{id}` | anyone in the org (users and service keys) |
| `POST /customers` (Idempotency-Key), `PATCH /customers/{id}` | signed-in users |
| `POST /customers/{id}/contacts`, `PATCH /customers/{id}/contacts/{cid}` | signed-in users |
| `GET /services`, `GET /services/{id}` (with the approved default price) | anyone in the org |
| `POST /services`, `PATCH /services/{id}` | signed-in users |
| `GET /price-lists`, `GET /price-lists/{id}` | anyone in the org |
| `POST /price-lists`, `PATCH /price-lists/{id}`, `PUT/DELETE /price-lists/{id}/items/{service_id}` | signed-in users, **draft lists only** |
| `POST /price-lists/{id}/copy` | signed-in users |
| `POST /price-lists/{id}/activate` (approve), `POST /price-lists/{id}/archive` | **owner / admin** |
| `GET /audit-events` | **owner / admin** |

Rules: approved price lists are frozen (409 `PRICE_LIST_LOCKED`; copy to change prices); approval needs at least one
price; service keys (n8n) are read-only (403) until scoped credentials exist (Phase E); every change writes an
audit event in the same transaction; lists are paginated (`limit` ≤ 100 / 200); unknown fields are rejected (422).

### Frontend

- New navigation item **งานขายและการเงิน / Sales & Finance** (`/qtc`). The phone bottom bar now sizes itself to the
  number of items (it was fixed at 7 columns and wrapped).
- `/qtc`: real counts (customers, services) and the approved price list, or a warning when there is none.
- `/qtc/customers`: search, status filter, pagination, create form (idempotency key per form), empty states.
- `/qtc/customers/[id]`: edit, archive / restore, contacts with main contact and channel consent, history (owners /
  admins).
- `/qtc/services`: catalogue, create form, price lists: price a draft, approve (owners / admins), copy.
- Loading, empty, error and **permission-denied (403)** states; amounts formatted from the exact decimal strings
  (never via JavaScript numbers); Thai tax ID checked in the form as well as on the server.

### Example data

`python -m app.cli seed-qtc-demo` (or `docker compose exec api python -m app.cli seed-qtc-demo`): 5 example
services, 2 example customers, and an example price list **as a draft** (a person must approve prices). Everything
is labelled "(ตัวอย่าง)"; running it again changes nothing.

## Tests

| Suite | Result |
|---|---|
| `tests/test_qtc_phase_a.py` on SQLite | 23 passed (run 3× for the concurrency test) |
| Phase A + concurrency tests on **PostgreSQL 17** (Docker) | 30 passed (run 2×) |
| Migrations on PostgreSQL | `0006 → 0008` upgrade, `alembic check` clean, downgrade to `0006`, upgrade again |
| Full backend suite (creative AI regression) | 121 passed |
| Frontend (Vitest) | 101 passed (11 new: amount formatting, tax ID, customer form, permission state) |
| ruff, mypy, ESLint, TypeScript, `next build` | clean; 18 pages built |
| Browser (dev stack) | created a customer through the form (invalid tax ID blocked, then C0001); priced and approved a draft list through the page (frozen afterwards, approved price shown on the service); C0002 from the restarted API; idempotent retry returned the same customer |

Covered by the Phase A tests: VAT and rounding, discounts, approval permissions (price lists), tenant isolation,
duplicate submissions (idempotency), concurrent numbering, pagination, validation, audit. The other areas in the
brief (quote revision, quote-to-order, payments, overpayments, cancellation, reconciliation, AI output validation)
belong to phases B–D and are **not tested yet** because they are not built yet.

## Security review (Phase A)

- Tenant isolation: every query filters by `organization_id`; cross-org reads and writes return 404 (tested,
  including pricing another org's service).
- Roles: price approval and the audit trail are owner / admin only; changes need a signed-in person; service keys are
  read-only. (The earlier hardening work made API keys a non-admin `service` role.)
- Prices: only approved lists will price quotations; approved lists cannot be edited; every price change is audited
  with before / after.
- Input: strict schemas (`extra="forbid"`), float amounts refused, tax ID checksum, code patterns.
- No external sending exists yet, so nothing can be delivered outside the app.

Open hardening items that will block external operations in later phases (from
`docs/PRODUCTION_HARDENING_REPORT.md`): no API-key revocation command; Windows worker pool without hard time limits;
budget reservations only for agent calls. External sending (Phase B quotation "send", Phase D reminders) must stay
disabled until scoped credentials, consent checks and approval gates are in place.

## Setup

```powershell
cd apps/api
uv run alembic upgrade head          # applies 0007 and 0008
uv run python -m app.cli seed-qtc-demo   # optional example data
```

Restart the API after upgrading (the dev server runs without auto-reload). Open `/qtc` in the dashboard.

## Decisions carried into the next phases

- Document numbers (QT-, SO-, INV-) will use `qtc_number_sequences` plus unique constraints.
- Quotation lines will be priced only from an approved price list; unknown services become lines that need a person
  to enter the price (the AI never sets one).
- Tax invoices and receipts stay separate from commercial invoices, behind a setting that is off until Thai legal and
  accounting requirements are verified.
