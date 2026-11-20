# Quote-to-Cash: implementation report

Date: 2026-11-20 · Phase A on `main` (`2186a66`); Phase B on `main`'s working tree (uncommitted at the time of writing)

## Status by phase

| Phase | Scope | Status |
|---|---|---|
| **A** | Customer CRM, service catalogue, rate cards (price lists), audit, numbering | **Done and tested** (SQLite and PostgreSQL) |
| **B** | Quotation engine, approval, revisions, acceptance, Thai PDF / DOCX | **Done and tested** (SQLite and PostgreSQL) |
| C | Sales orders, invoices, payment tracking | Not started |
| D | AI agents (SalesIntake, Quotation, Finance, Collection) and Mira | Not started |
| E | n8n workflows, QTC dashboard, end-to-end tests | Not started |

Nothing beyond Phase B is claimed. The `/qtc` overview lists phases C–E as "Planned" text, not as buttons.

## Phase B: what exists

### Database (migration `0009`)

| Table | Purpose |
|---|---|
| `quotations` | Header: number `QT-YYYY-NNNN` (unique per org, from the locked counter), revision, customer, contact, approved price list, VAT rate and treatment (copied from the price list), issue date, valid until, notes, terms, totals (`NUMERIC(18,2)`), who submitted / approved / sent, acceptance evidence, idempotency key. Status check: draft, pending_approval, approved, sent, accepted, declined, cancelled ("expired" is computed). |
| `quotation_lines` | Service or free item, quantity, unit, unit price (**NULL when unknown**), `price_source` (price_list / manual / missing), percent and fixed discount, VAT exempt, line net / VAT / total. Checks: quantity > 0, price ≥ 0, discount 0–100 %. |
| `quotation_revisions` | Frozen JSON snapshot of each submitted revision (customer, lines, totals as exact strings); unique `(quotation_id, revision)`. |
| `quotation_approvals` | Every decision (approved / changes requested), revision, comment, who, when. |
| `qtc_settings` | Seller details printed on documents (registered name, tax ID, branch, address, contacts), default terms and validity. |

### Rules

- **Prices are never invented.** A service line takes its price from the quotation's approved price list. A typed
  price is kept as `manual` and recorded in the audit trail. Anything else stays **missing**; submitting returns
  `422 PRICE_REQUIRED` with the line numbers until a person enters it.
- **Brief matching** only picks catalogue services by the keywords, names and codes the organization entered
  (deterministic). Items that are not in the catalogue (e.g. travel costs) are added by a person.
- **Totals** come from `money.py` (per-line half-up rounding, VAT added or extracted) and are recomputed from the
  stored lines on submission, so the frozen revision always matches the rules. Amount in Thai words is included.
- **States:** draft → pending_approval → approved → sent → accepted / declined; cancel any time before acceptance.
  Approval by a signed-in owner or admin only. "Send back for changes" opens the next revision so the version the
  approver saw stays frozen. `revise` starts a new draft revision from a submitted, approved, sent, declined or
  expired quotation. Acceptance records name, method (signed document, PO, email, LINE, verbal), reference,
  evidence link and the revision accepted; an expired quotation returns `409 QUOTATION_EXPIRED`; a repeated
  acceptance returns the same result.
- **Concurrency:** every transition locks the row and then claims it with a conditional
  `UPDATE … WHERE status IN (…)`, so of parallel approvals or acceptances exactly one takes effect (found by
  testing: on SQLite the row lock alone let 4 parallel approvals all succeed).
- **No external sending.** "Send" records how a person delivered the approved document.
- **Documents:** `GET /quotations/{id}/document?format=pdf|docx[&revision=n]` renders Thai PDF (ReportLab with the
  existing Thai font and tone-mark handling) and DOCX (python-docx, Tahoma): seller and customer with tax IDs,
  lines, totals, VAT, amount in words, notes, terms, signature blocks. Drafts and unapproved revisions carry a
  "ฉบับร่าง ยังไม่อนุมัติ" watermark and are only rendered for signed-in users; integration keys get 403. Responses
  are `private, no-store` and every download is audited.

### API added

`GET/PUT /settings` (PUT: owner/admin) · `GET/POST /quotations` (status filter incl. expired, customer, search,
pagination, Idempotency-Key) · `GET/PATCH /quotations/{id}` (edit drafts only) · `POST /quotations/{id}/submit` ·
`/approve` (owner/admin) · `/send` · `/accept` · `/decline` · `/revise` · `/cancel` · `GET /quotations/{id}/revisions` ·
`/approvals` · `/document`.

### Frontend added

- **Quotations** tab: list with status filters (including expired), missing-price marker, totals.
- `/qtc/quotations/new`: customer, subject, customer request (matched to services), line editor showing the approved
  price or "needs a price" per line; typed prices labelled "entered by a person"; idempotent creation.
- `/qtc/quotations/[id]`: lines with price source, server totals and amount in words, missing-price alert, actions
  for the current state (edit, submit, approve / send back, record sent, record acceptance, decline, revise,
  cancel), PDF / DOCX downloads (per revision), revision and approval history.
- Overview shows quotations waiting for approval.

### Phase B tests

| Suite | Result |
|---|---|
| `tests/test_qtc_phase_b.py` on SQLite | 24 passed (run 3×) |
| Phase A + B + concurrency on **PostgreSQL 17** | 55 passed (run 2×) |
| Migrations `0006 → 0009` on PostgreSQL | upgrade, `alembic check` clean, downgrade, upgrade |
| Full backend suite | 145 passed |
| Frontend | 112 passed (6 new: line helpers, line editor states, totals) · lint, types, build (21 pages) clean |
| Browser (dev stack) | QT-2026-0001: brief matched the service and priced it from the approved list; a travel line added with a typed price ("entered by a person"); totals 46,500.50 + VAT 3,255.04 = 49,755.54 with Thai words; submit → approve → record sent → record acceptance (PO reference) through the page; PDF and DOCX downloads through the proxy without the draft marker after approval |

Covered: VAT and rounding, discounts, VAT-inclusive prices, missing and manual prices, approval permissions,
send-back revisions, state machine, expiry, revision snapshots, acceptance evidence, documents and watermark,
integration access to documents, tenant isolation, idempotent creation, concurrent numbering, concurrent approval
and acceptance, Thai amount in words.

**Not verified:** the PDF layout was not looked at by eye in this environment (no PDF renderer available); the
DOCX text and the PDF structure are tested. A sample was sent for review.

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
belong to phases B–D (quote revision is now covered by Phase B; the rest is **not tested yet** because it is not
built yet).

## Security review (Phases A and B)

- Quotations: approval by signed-in owner / admin only (members and service keys get 403); self-approval is allowed
  for small teams but recorded (`self_approved` in the audit event); unapproved documents are never rendered for
  integration keys and always carry the draft watermark; documents are not cached (`private, no-store`); the system
  sends nothing to customers ("send" is a record of what a person did); prices come only from approved lists or are
  typed by a person and audited.

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
budget reservations only for agent calls. External sending (emailing a quotation from the system, Phase D
reminders) must stay disabled until scoped credentials, consent checks and approval gates are in place; Phase B
deliberately has no such sending.

## Setup

```powershell
cd apps/api
uv run alembic upgrade head          # applies 0007, 0008 and 0009
uv run python -m app.cli seed-qtc-demo   # optional example data
```

Restart the API after upgrading (the dev server runs without auto-reload). Open `/qtc` in the dashboard.

## Decisions carried into the next phases

- Document numbers (QT-, SO-, INV-) will use `qtc_number_sequences` plus unique constraints.
- Quotation lines will be priced only from an approved price list; unknown services become lines that need a person
  to enter the price (the AI never sets one).
- Tax invoices and receipts stay separate from commercial invoices, behind a setting that is off until Thai legal and
  accounting requirements are verified.
