# MCR AI Workforce: current state audit

Audit date: 2026-11-20. Scope: the code in `C:\MCRAIWorkforce` and the app running at
`http://localhost:3000/assistant`. Nothing was changed for this audit; this file is the only output.

Legend used throughout:

- **Implemented**: real code path, exercised by tests and/or used live.
- **Implemented, untested in production setting**: code exists and is tested against fakes or SQLite, but
  has not run against the real service or the production stack.
- **Demo / placeholder**: an explicit `DEMO_MODE` path that produces labelled placeholder output.
- **Missing**: not present in the code.

File references are relative to `apps/api/app/` (backend) or `apps/web/src/` (frontend) unless stated.

## Phase 0 progress (2026-11-20)

| Item | State |
|---|---|
| Version control | Git repository initialised (`main`); `.gitignore` excludes secrets (`.env`), data, local logos and generated posters; `.gitattributes` normalises line endings. Not committed yet |
| CI | `.github/workflows/ci.yml`: API lint/types/tests on SQLite; API tests + migrations on PostgreSQL 17 and Redis 7; web lint/types/unit tests/build. Not run yet (no remote) |
| Permission gaps | Fixed: approve and asset delete need owner/admin; unapproved files are released only to people (review), API keys get no link until approval (`released` flag on files, "Draft" badge in the UI) |
| Worker supervision | `scripts/run-worker.ps1` runs worker + beat and restarts them (verified by killing the worker); worker heartbeats in the database (migration 0005); dashboard, Settings and Mira warn when no worker is alive |
| Frontend tests | Vitest with 18 tests for intent detection, login redirect and proxy path safety, poster draft logic |
| PostgreSQL / Redis | Not available on this machine (no Docker); covered by the CI job instead |

---

## 0. Summary

MCR AI Workforce is a working multi-agent content-production system, not a mockup. A command such as
"3 promotional images and 5 Thai Facebook captions" is planned into a task graph, executed by Celery
workers through six agents, checked by a QA agent, and held for human approval, with every paid call
budget-checked and costed. The dashboard (Next.js 16) and a voice-first assistant ("Mira") sit on top.

Strong foundations to keep:

- A database-backed orchestrator with leases, heartbeats, retries with backoff, a reaper, idempotent job
  creation and duplicate-safe task claims.
- Hybrid image routing (local ComfyUI first, cloud fallback by policy) with data-classification rules.
- Per-job and monthly budgets, usage events and estimate-vs-actual cost on jobs and tasks.
- Tenant isolation on every authenticated query; encrypted provider secrets; HMAC-signed file links.
- 80 backend tests with HTTP-level provider fakes.

Main gaps for production-grade "multi-agent automation":

- The orchestrator only knows one workflow shape (plan → content/image/document/video → QA → approval).
  There is no general workflow/agent registry, no tool-calling agents and no human-in-the-loop step other
  than final approval.
- n8n integration is one-directional (n8n polls the API); the API sends no webhooks/events.
- Quote-to-cash does not exist (no customers, products, prices, quotes, invoices or payments).
- Approval does not gate anything: files are downloadable before approval, and any member can approve.
- The production stack (PostgreSQL, Redis, MinIO, Docker, Caddy) has never been run; SQLite and the
  filesystem broker hide locking and concurrency issues.
- No frontend tests; no version control (the folder is not a git repository).

---

## 1. Frontend architecture

| Item | State |
|---|---|
| Framework | Next.js 16.4.6 (App Router, Turbopack), React 19.3, TypeScript strict, Tailwind CSS 4.3 |
| 3D | three.js 0.170 (Mira avatar, loaded on demand, WebGL fallback) |
| Build output | `output: "standalone"` for Docker; `cacheComponents` and `partialPrefetching` on but effectively unused (all app pages are client components) |
| Checks | `lint` (ESLint 9, next core-web-vitals) and `typecheck`. **No test library, no tests.** No Prettier config |
| React Compiler | Not enabled |
| i18n | Own dictionary (`i18n/dictionaries.ts`, ~1,270 lines). Thai is typed against English keys, so a missing Thai key is a type error. Locale in the `mcr_locale` cookie |

Folder structure (`apps/web/src`):

| Path | Purpose |
|---|---|
| `proxy.ts` | Next 16 proxy (old middleware): redirects to `/login` without a session cookie; rewrites signed file URLs to the API |
| `app/layout.tsx` | Root layout, fonts (Inter, Noto Sans Thai), `I18nProvider` |
| `app/login/` | Login page and form (`safeNext` prevents open redirects) |
| `app/(app)/` | Signed-in pages inside `AppShell`: assistant, dashboard, workforce, jobs, jobs/[id], posters, deliverables, settings |
| `app/api/proxy/[...path]/route.ts` | Generic passthrough to the API, `v1/...` only, cookie → `Authorization: Bearer` |
| `app/api/auth/login`, `logout` | Set and clear the httpOnly `mcr_session` cookie |
| `components/` | `ui.tsx` (design primitives), `AppShell`, `AssistantAvatar` (Mira), `AgentGraph` (SVG job graph), `PosterChat`, `LogoUploadButton`, `TournamentFields`, `artifacts.tsx`, `status.tsx` |
| `lib/` | `api.ts` (client + errors + idempotency keys), `server.ts`, `useApi.ts` (fetch + polling), `poster.ts` (poster logic and hooks), `voice.ts`, `useSpeechInput.ts`, `types.ts` |

Backend communication: browser → `/api/proxy/v1/...` → API with the bearer token from the httpOnly cookie.
Bodies are fully buffered (no streaming). Signed files are relayed by rewrite so Range requests work.

Pages (all use real API data; "demo" appears only when the API reports `demo_mode`):

| Page | What it does |
|---|---|
| `/assistant` (landing) | Mira, the voice assistant (see §2) |
| `/dashboard` | Summary counts, monthly spend vs budget, latest jobs and files (15 s polling) |
| `/workforce` | New job form: command, agents, budget, language, aspect ratio, data classification, approval; voice dictation with a spoken submit phrase and 3 s cancel countdown |
| `/jobs`, `/jobs/[id]` | Job list; Job Monitor with tasks, events, QA report, agent graph, approval panel, cancel/delete (3 s polling) |
| `/posters` | Poster designer: form, logo/artwork library, Mira chat, live server-rendered preview, two layouts (9:16 esports, 4:5 tournament) |
| `/deliverables` | All files, filter by kind, delete all |
| `/settings` | AI providers (OpenAI, ComfyUI, Firefly), worker status, organisation budgets and policies, password change, usage breakdown |

## 2. Assistant page (`app/(app)/assistant/page.tsx`, ~990 lines)

Implemented:

- **Mira avatar**: procedural three.js character with idle, listening, thinking and speaking modes;
  happy and sad expressions on job outcome; reduced-motion support; WebGL fallback.
- **Voice in**: Web Speech API (`lib/voice.ts`). Not available in Firefox; Chromium sends audio to the
  browser vendor's cloud.
- **Voice out**: cloud TTS through `POST /v1/assistant/speech` (OpenAI `gpt-4o-mini-tts`), then browser
  `speechSynthesis`, then a silent pause sized to the caption.
- **Welcome briefing**: greets the user by first name and reads running / awaiting-approval / failed job
  counts and month spend. Plays on open when the browser allows sound, else on the first click or key.
- **One-press talk and barge-in**: NEW CHAT listens at once; while Mira speaks the button (or Space)
  interrupts her and listens.
- **Confirmation before spending**: the command is repeated back; "yes / no / design" by voice or buttons;
  typed fallback. Jobs are created with an idempotency key.
- **Poster design stage**: chat with Mira (LLM fills the poster draft), live preview, layout switch,
  logo/artwork/character uploads, artwork move/zoom by voice, hand-off to `/posters`.
- **Job graph stage**: live agent graph (zoom, pan, fit), outcome announcement, finished posters inline.
- **Full screen**: Fullscreen API with a fill-the-window fallback (iOS Safari, embedded browsers).

Browser dependencies: WebGL, Web Speech recognition, speech synthesis voices (a Thai female voice exists
in Edge), autoplay/user activation, Fullscreen API, sessionStorage.

Limits: the page is one ~35-handler component that relies on refs to avoid stale closures; it has no
tests; intent detection (`decide`, `posterIntent`) is keyword matching.

## 3. Agents and their real capabilities

Six agents are registered (`worker/runner.py:20-27`). The poster modules are layout libraries used by
the image agent, not agents.

| Agent | Output | Providers | Without provider |
|---|---|---|---|
| planner | JSON plan (tasks, waits, costs) | OpenAI JSON (temperature 0.1); otherwise a Thai/English rule parser | Rules always work. Poster jobs skip the LLM |
| content | Captions (`caption-NN.txt`), `content.json`, YouTube metadata | OpenAI chat | Demo captions, or `PROVIDER_NOT_CONFIGURED` |
| image | PNG, cropped to aspect; posters composed on top (2 layouts, Thai typesetting, logos, QR code) | ComfyUI (local SDXL), Adobe Firefly, OpenAI `gpt-image-2`; user artwork or characters at $0 | Labelled gradient placeholder |
| document | PDF (ReportLab) + DOCX (python-docx): proposal, quotation draft, etc. | OpenAI chat | Demo sections with "DEMO — NOT FINAL" watermark |
| video | MP4 (H.264/AAC), script, storyboard, SRT, scene manifest, voiceover MP3 | OpenAI chat + TTS, local FFmpeg | Demo script, silent track, DEMO stamp |
| qa | `qa-report-rN.json` with PASS / REVISE / HUMAN_REVIEW | Deterministic checks (hashes, decode, structure, codecs, duration); optional LLM caption review | Deterministic checks only |

Notes:

- Prices are never generated: the quotation document deliberately writes "to be confirmed".
- Video is template-based (slides, hard cuts, burned-in captions); no motion or music.
- Thai text: manual tone-mark lifting without libraqm; Thai word segmentation is missing in PDFs.
- AI poster artwork is kept in the asset library so posters can be re-laid out for free.

## 4. API endpoints and backend services

Routers (`main.py:70-78`). Auth: `X-API-Key` (always admin) or Bearer JWT; `require_admin` = owner/admin.

| Area | Endpoints |
|---|---|
| Auth | `POST /v1/auth/login`, `POST /v1/auth/change-password`, `GET /v1/auth/me` |
| Settings | `GET /v1/settings` (probes providers live), `PATCH /v1/settings/organization` (admin), `PUT /v1/settings/providers/{openai,comfyui,firefly}` (admin) |
| Health | `GET /health/live`, `GET /health/ready` (DB, broker, storage) |
| Jobs | `POST/GET /v1/jobs`, `GET /v1/jobs/{id}`, `/tasks`, `/artifacts`, `/qa`, `GET/POST /events` (POST only `n8n.*`), `POST /cancel`, `/approve`, `/plan`, `/dispatch`, `DELETE /jobs/{id}` and `DELETE /jobs` (admin) |
| Files | `GET /v1/artifacts`, `PUT /artifacts/{id}/text`, `POST /artifacts/{id}/regenerate`, `POST /artifacts/{id}/regenerate-caption` (synchronous LLM), `DELETE /artifacts` (admin), public signed `GET /v1/artifacts/{id}/download` |
| Reporting | `GET /v1/dashboard/summary`, `GET /v1/usage?from&to` |
| Assets / posters | `POST/GET/DELETE /v1/assets`, public signed `GET /v1/assets/{id}/file`, `POST /v1/posters/preview` (free), `POST /v1/posters/chat` (LLM) |
| Assistant | `POST /v1/assistant/speech` (TTS, budget-checked) |

Not available over HTTP: user management, invitations, API-key management, projects CRUD, token refresh,
webhooks. These exist only as CLI commands (`app/cli.py`: `seed`, `create-api-key`, `generate-secrets`,
`reap`).

Services: `services/orchestrator.py` (state machine, 618 lines), `services/router.py` (provider routing,
budgets), `services/storage.py` (local / S3), `services/media.py` (FFmpeg, fonts), `services/thai_text.py`,
`services/brand_assets.py`.

## 5. n8n integration

Implemented on the n8n side only (`n8n/workflows/mcr-ai-workforce.json`, generated by
`n8n/build_workflow.py`, documented in `docs/N8N.md`):

1. n8n webhook `POST /webhook/mcr-ai-workforce` with a static `X-Webhook-Token`.
2. `POST /v1/jobs` with `auto_start=false` and `Idempotency-Key` from `request_id`; responds 202.
3. `POST /plan`, poll every 3 s; `POST /dispatch`.
4. Poll `GET /jobs/{id}` every 10 s (gives up after ~1 hour), read `/qa`, write `n8n.workflow_completed`
   to the job events, optionally notify `MCR_NOTIFY_WEBHOOK_URL`.

API support for it: `/plan` and `/dispatch` for staged starts, the `n8n.` event prefix, API keys named
`n8n*` shown as actor "n8n".

**Missing**: outbound webhooks or an event stream from the API (n8n must poll), HMAC-signed callbacks,
inbound triggers other than job creation, per-key scopes (the n8n key is a full admin), and any n8n
workflow for business processes (CRM, LINE, accounting). n8n is not running on this machine (nothing on
port 5678); the workflow has not been imported or run end to end.

## 6. Database schema and job persistence

SQLAlchemy models (`models.py`), Alembic revisions `0001` (initial), `0002` (brand assets, job options),
`0003` (asset kind), `0004` (asset source). Applied on SQLite only.

| Table | Purpose / key columns |
|---|---|
| organizations | Budgets (monthly 50, per job 5 by default), cloud-fallback and restricted-data policies, business context |
| users, memberships | bcrypt users; roles owner / admin / member per org |
| api_keys | SHA-256 hash, prefix, revoked_at; no scopes |
| projects | Brand guidelines JSON (no CRUD endpoints) |
| jobs | Command, status, plan / options / qa_report JSON, budget, estimated and actual cost, approval, data classification, idempotency key (unique per org) |
| tasks | Agent, params / result JSON, provider, execution location, attempts, lease and heartbeat, costs |
| task_dependencies | Hard (`depends_on`) or soft (`waits_for`) edges |
| artifacts | Kind, storage key, sha256, is_demo, text preview, metadata JSON |
| brand_assets | Logos and artwork (`kind` logo/artwork, `source` upload/ai) |
| approvals | pending / approved / rejected, reason, comment, decided_by |
| usage_events | Agent, provider, operation, units, cost; kept when a job is deleted |
| provider_configs | Fernet-encrypted secret + config per org |
| audit_logs | Actor, action, detail |

Job statuses: queued, planning, planned, running, awaiting_approval, completed, partial, failed,
cancelling, cancelled, rejected. Task statuses: pending, queued, running, succeeded, failed, skipped,
cancelled.

## 7. Background tasks and concurrency

Implemented:

- Celery with queues per agent plus `maintenance`; `acks_late`, `reject_on_worker_lost`, prefetch 1,
  soft/hard time limits 1500/1800 s (not enforced on Windows threads pool).
- Retries live in the database: exponential backoff `min(5·2^(n-1), 300)` s, 3 attempts, dead-letter as a
  failed task plus an audit event.
- Atomic task claims (`UPDATE … WHERE status='queued'`) and an owner check on every result write, so
  duplicate deliveries are harmless.
- 300 s leases extended by heartbeats; a reaper every 30 s requeues or fails expired tasks.
- Compose layout: core workers ×4, image ×2, video ×1, maintenance ×1 with beat.

Gaps and risks:

- Development runs use the filesystem broker and SQLite, where `FOR UPDATE` / `SKIP LOCKED` do nothing.
  Concurrent dispatch is untested.
- The final FFmpeg render can run for up to 900 s without a heartbeat while the lease is 300 s; the reaper
  can then requeue a still-running task, duplicating work.
- No per-organisation or per-provider concurrency limits (only worker counts and a ComfyUI queue-depth
  gate).
- No result backend; no workflow-level timeouts or SLA alerts.
- On this machine the Celery worker has stopped by itself several times (exit codes 4 and 107 seen during
  development) and must be restarted by hand; there is no supervisor.

## 8. AI providers and routing

| Provider | Use | State |
|---|---|---|
| OpenAI | Chat JSON (planner, content, document, video script, QA, poster chat), images `gpt-image-2`, TTS `gpt-4o-mini-tts` | Implemented; used live during development |
| ComfyUI | Local SDXL text-to-image (built-in or custom workflow) | Implemented; verified on a local RTX 5080 |
| Adobe Firefly | Images (image3, image4) via Firefly Services | Implemented, untested against the real service (needs an enterprise Firefly Services licence) |
| Local LLM (Ollama, vLLM), Anthropic, Gemini, ElevenLabs, Whisper | — | Missing |

Routing (`services/router.py`):

- Images: ComfyUI first when healthy, then Firefly and OpenAI as fallbacks if the org allows cloud
  fallback; either cloud provider can be set to "first".
- Text: cloud only. With no LLM the planner and QA fall back to rules; content/document/video fail or use
  demo mode.
- `restricted` data never goes to cloud providers unless the org explicitly allows it.
- Every paid call runs `ensure_budget` (job budget and monthly budget) first.

Gaps: no local LLM path, so "hybrid" applies to images only; the budget check is not atomic; price tables
live in environment settings, not per provider in the database; ComfyUI is costed at $0 (no GPU/energy
cost model); provider HTTP clients are created per call and not closed.

## 9. Authentication, organisation isolation and security

Implemented:

- bcrypt passwords (SHA-256 pre-hash), constant-time login, HS256 JWT (12 h) in an httpOnly SameSite=Lax
  cookie held by the Next.js server.
- Every authenticated query filters by organisation; public file links are HMAC-signed (15 min).
  Tenant-isolation tests exist.
- Provider secrets encrypted with Fernet and never returned; audit log for logins, settings, jobs,
  approvals and assets.
- Rate limits (240/min per principal, 10/min login, 30/min bad API keys), in memory or Redis.
- CORS allow-list; production config guard refuses dev secrets, SQLite, the filesystem broker and eager
  mode.

Gaps (verified in code where marked ✔):

- ✔ `POST /jobs/{id}/approve` needs only a signed-in user (`api/jobs.py:286`): members can approve.
- ✔ `DELETE /v1/assets/{id}` is open to members (`api/assets.py:120`).
- ✔ Download links are issued whether or not a job is approved (`schemas.py:334`): approval does not gate
  delivery.
- API keys are always admin with no scopes; no key rotation endpoint.
- No refresh tokens or revocation, no MFA, no password reset, no invitations, no org switching (login
  always picks the first membership).
- No explicit CSRF token (relies on SameSite=Lax + JSON).
- Behind Caddy, rate limiting may see one client IP unless forwarded IPs are trusted.

## 10. Implemented vs placeholder vs missing

| Capability | State |
|---|---|
| Command → plan → parallel agents → QA → approval | Implemented |
| Captions, images, posters, PDF/DOCX, MP4 with voiceover | Implemented (OpenAI/ComfyUI); demo placeholders without providers |
| Voice assistant Mira, poster designer, agent graph | Implemented (browser-dependent voice) |
| Regenerate image / document / video / caption | Implemented (regeneration bypasses leases; see debt) |
| Firefly images | Implemented, untested against the real service |
| PostgreSQL, Redis, MinIO, Docker Compose, Caddy HTTPS | Configuration written, never run |
| n8n workflow | Written, never imported or run end to end |
| Outbound webhooks / event stream | Missing |
| Quote-to-cash (customers, products, prices, quotes, invoices, payments) | Missing (only a quotation document draft without prices) |
| Per-step human approval, approval gating delivery | Missing |
| User and role management UI, API-key scopes | Missing |
| Local LLM | Missing |
| Frontend tests, end-to-end browser tests | Missing |
| Version control | Missing (not a git repository) |

---

## 11. Reusable components

Backend:

- `services/orchestrator.py`: idempotent job creation, DAG tasks with hard/soft edges, claims, leases,
  retries, reaper, finalize/approval. The core to build multi-agent orchestration on.
- `agents/base.py` `AgentContext`: heartbeats, cancellation, `record_usage`, `save_artifact`, temp dirs.
- `services/router.py`: provider routing with privacy and budget rules (extend to LLMs and new providers).
- `providers/*`: thin HTTP clients with error mapping (`AUTH_FAILED`, `NO_CREDIT`, retryable).
- Usage events + job/task cost columns; audit log; signed URLs; encrypted provider configs.
- `agents/document.py` (PDF/DOCX with Thai fonts): the base for quotations and invoices.
- `agents/poster*.py` and `services/thai_text.py`: Thai typesetting reusable for any branded document.

Frontend:

- `components/ui.tsx` primitives, `status.tsx`, `artifacts.tsx` (cards, regenerate, edit).
- `lib/useApi.ts` (polling fetch), `lib/api.ts` (errors, idempotency keys), `lib/poster.ts` hooks.
- `AgentGraph` (live DAG view with zoom/pan) and the approval panel in `JobMonitor`.
- `AssistantAvatar` and the voice helpers (`lib/voice.ts`).

## 12. Technical debt

| Debt | Where | Impact |
|---|---|---|
| No version control | repository root | No history, review or rollback. Highest priority to fix |
| No frontend tests | `apps/web` | Regressions in the assistant and poster flows go unnoticed |
| Large files | `assistant/page.tsx` ~990, `settings/page.tsx` ~1,060, `posters/page.tsx` ~860, `orchestrator.py` 618, `test_workforce.py` 1,425 (single test file) | Hard to change safely |
| Blocking work in API requests | `regenerate-caption`, `posters/chat`, `assistant/speech`, poster preview, `upload_asset` (async route with sync DB/PIL) | Slow requests block workers; poor under load |
| Lease vs long FFmpeg render | `agents/video.py` final render | Duplicate work when the reaper requeues |
| Non-atomic budget check | `services/router.py:73-89` | Parallel tasks can overshoot budgets slightly |
| Regeneration outside claim/lease | `worker/regenerate.py` | Partial failures can leave orphan files |
| Duplicated logic | image cost order (planner vs router); budget + usage code in assistant and poster chat; upload helpers in `posters/page.tsx` vs `lib/poster.ts`; two speech-recognition wrappers; job-creation code in three pages | Drift and bugs |
| Raw `fetch` bypassing `api()` | `voice.ts`, `poster.ts`, `posters/page.tsx`, `artifacts.tsx` | No consistent 401 handling |
| Hard-coded values | brand dict in `config.py`; `/posters` sends `language: "th"` | Wrong language for English users |
| SQLite-only verification | migrations, locking | Unknown issues on PostgreSQL |
| Worker process supervision | local Windows runs | Worker stops silently; jobs stall |
| Outdated docs | `docs/LIMITATIONS.md` (one poster layout, agent graph "optional") | Misleading |

---

## 13. Prioritised integration roadmap

Principles: keep the FastAPI + Celery + orchestrator core, the provider router and the Next.js shell;
extend rather than replace. Each phase ends with tests and an updated `docs/`.

### Phase 0: foundations (1–2 weeks, prerequisite for everything)

1. Put the project under git; add CI running `ruff`, `mypy`, `pytest`, `eslint`, `tsc`.
2. Bring up `compose.yaml` (PostgreSQL, Redis, MinIO); run migrations and the suite on PostgreSQL; add a
   concurrency test for dispatch and claims.
3. Supervise workers (Compose restart policies; on Windows a service wrapper) and add a worker-heartbeat
   alert in Settings.
4. Fix the three verified authorisation gaps: approval and asset deletion require admin; gate downloads
   on approval (or mark unapproved links "draft").
5. Add frontend tests for pure helpers (`decide`, `posterIntent`, `safeNext`, `mergeDraft`) and one
   Playwright smoke test (login → job → approval).

### Phase 1: background jobs and deliverables (1–2 weeks)

1. Heartbeat during long FFmpeg renders (read progress from FFmpeg) so leases never expire mid-render.
2. Run regeneration through the normal claim/lease path; write new files atomically.
3. Move blocking request work (poster chat, caption rewrite, speech, preview) behind short Celery tasks or
   async clients; make `upload_asset` a sync route.
4. Retry failed task from the dashboard; a dead-letter list with replay.
5. Retention policy for artifacts (per org, e.g. 90 days) and storage usage in Settings.

### Phase 2: cost tracking and approval workflows (1–2 weeks)

1. Atomic budget reservation: reserve the estimate on the job row (`UPDATE … WHERE actual+reserved+est
   <= budget`) before a paid call, settle after.
2. Price table per provider in the database (editable in Settings), including a ComfyUI GPU cost per
   image so local work shows a real cost.
3. Approval policies: approval required above a cost threshold, per data classification, or per
   deliverable type; multi-step approvals (e.g. marketing then manager); approver roles.
4. Approval gates delivery and outbound events (publish only after approval).
5. Cost reports: per project, per agent, per provider, month-to-date forecast, budget alerts at 80%.

### Phase 3: n8n workflow integration (1–2 weeks)

1. Outbound webhooks from the API: subscriptions per org (`job.planned`, `task.failed`,
   `job.awaiting_approval`, `job.completed`, `approval.decided`), HMAC-SHA256 signatures, retries with
   backoff, a delivery log. This removes n8n polling.
2. Scoped API keys (`jobs:write`, `jobs:read`, `approvals:write`) with rotation in Settings; give n8n a
   least-privilege key.
3. Inbound generic trigger: `POST /v1/triggers/{name}` mapping a payload to a job template (so n8n, LINE OA
   or a form can start jobs without building the command string).
4. Ship tested n8n templates: LINE/Facebook post publishing after approval, Google Drive delivery,
   notification to LINE/Slack, nightly cost report.
5. Run the existing workflow end to end against the Compose stack.

### Phase 4: multi-agent orchestration (3–4 weeks)

1. Agent registry: agent metadata (inputs, outputs, cost estimator, queue) replacing the hard-coded six;
   new agents register without touching the orchestrator.
2. Job templates / workflow definitions stored in the database (DAG of agent steps, with parameters),
   alongside the current planner, so recurring processes are explicit and versioned.
3. Human-in-the-loop steps inside a DAG (approval or input task type that pauses the branch).
4. Planner upgrade: structured tool-calling with validation against the registry; keep the rule parser as
   the offline fallback.
5. Use hard `depends_on` where outputs are consumed (document needs content), keep `waits_for` for
   ordering; add per-org and per-provider concurrency limits.
6. Mira routes spoken requests to templates ("make the monthly tournament posters") as well as free
   commands.

### Phase 5: hybrid cloud/local AI routing (2–3 weeks)

1. Local LLM provider (Ollama or vLLM, OpenAI-compatible API) in `providers/` and `route_llm`, so
   `restricted` data can still get text generation locally.
2. Routing policy per task type: quality / cost / privacy profiles (e.g. captions local, legal text cloud).
3. Health and capacity-aware routing for ComfyUI (queue depth exists) and the local LLM; circuit breakers
   per provider; cached health instead of probing on every request.
4. Quality gates: QA can request a cloud re-run when local output fails checks.
5. Report local vs cloud share and cost savings in the usage page.

### Phase 6: quote-to-cash automation (4–6 weeks; depends on Phases 0–3)

1. Data model: customers, contacts, products/services with price lists (THB, VAT 7%), quotes and quote
   lines, sales orders, invoices, payments, with organisation isolation and audit like existing tables.
2. Quote agent: drafts a quotation from a request using the price list (prices come from the database,
   never from the LLM); PDF/DOCX via the existing document agent; human approval before sending.
3. Quote → order → invoice flow with numbering, tax invoice fields required in Thailand, credit notes.
4. Payments: record transfers manually first; later PromptPay QR on invoices and bank/payment-gateway
   webhooks through n8n; reconciliation.
5. Accounting export (e.g. FlowAccount or CSV) and dunning reminders via n8n (LINE/email).
6. Dashboard: pipeline value, quotes awaiting approval, overdue invoices, cash received.

Suggested order: 0 → 1 → 2 → 3 → 4 → 5 → 6. Phases 3 and 5 can run in parallel with 4 once Phase 2 is
done; quote-to-cash should start only after approvals gate delivery and webhooks exist, because sending
quotes and invoices to customers must be approval-controlled and auditable.
