# Production hardening report

Date: 2026-11-20 · Baseline commit: `2bcc81b` (clean tree, secrets excluded by `.gitignore`)

**Status: NOT production-ready.** Unit, concurrency and frontend tests pass on SQLite and the local dev
stack. The integration run against PostgreSQL, Redis, MinIO and n8n (items 6, 7 and 10) has **not** been
executed yet because Docker is not installed on this machine. The script for it is ready
(`scripts/integration-check.ps1`).

## Summary by item

| # | Item | State |
|---|------|-------|
| 1 | Git initialised safely | Done in the baseline commit. `.gitignore` excludes `.env*` (except `.env.example`), databases, storage, generated posters, logos, logs, coverage, local Claude settings. `.gitattributes` normalises line endings. |
| 2 | Only owner/admin approves jobs and deletes shared assets | Done, tested |
| 3 | Unapproved deliverables not downloadable | Done, tested |
| 4 | FFmpeg lease heartbeats | Done, tested |
| 5 | Atomic budget reservations | Done, tested |
| 6 | PostgreSQL / Redis / MinIO via Docker Compose | Prepared (`compose.integration.yaml`), **not run** |
| 7 | Alembic migrations on PostgreSQL | Prepared (script + CI job), **not run locally** |
| 8 | Concurrency tests | Done (`tests/test_concurrency.py`, 8 tests) |
| 9 | Frontend tests for Assistant and approval flow | Done (Vitest + Testing Library) |
| 10 | n8n workflow against the running API | **Not run** (needs Docker + an n8n API key, which needs your OK) |

## Security issues fixed

1. **Any member could approve a job or delete shared brand assets.** `POST /v1/jobs/{id}/approve` and
   `DELETE /v1/assets/{id}` now require the owner or admin role (`require_admin`). Members get
   `403 FORBIDDEN`; the dashboard shows the server's message.
2. **Drafts awaiting approval could be downloaded with a signed URL.** `GET /v1/artifacts/{id}/download`
   now returns `403 NOT_RELEASED` unless the job is `completed` or `partial`. Job responses give drafts no
   signed URL.
3. **API keys (e.g. n8n) received draft content.** For unreleased jobs, API-key callers get no
   `download_url` and no `text_preview`.
4. **Reviewers still need to see drafts** — new `GET /v1/artifacts/{id}/preview`: signed-in user of the
   same organisation only, API keys refused for drafts, `Cache-Control: private, no-store`. The dashboard
   reaches it through the authenticated proxy (`/api/proxy/...`), never the public signed-file route.
5. **Proxy hardening.** The dashboard proxy now streams bodies and forwards only `range`/`if-range` and
   the needed response headers (`content-type`, `content-length`, `content-range`, `accept-ranges`,
   `content-disposition`). Path-escape and open-redirect guards are covered by tests.

## Reliability issues fixed

- **FFmpeg renders could lose their lease.** `run_ffmpeg()` (`app/services/media.py`) runs FFmpeg with
  `Popen`, calls `on_wait` (the task heartbeat) every 30 s, enforces a deadline, and writes stderr to a
  temp file so a chatty FFmpeg cannot block on a full pipe. The video agent passes `ctx.heartbeat`.
- **Budget race (check-then-spend).** `app/services/budget.py` reserves the estimated cost before a paid
  call: locks the organisation row, checks the monthly limit (spend + reservations), then a conditional
  `UPDATE jobs ... WHERE actual + reserved + amount <= budget_limit`. `record_usage` settles the
  reservation; `AgentContext.cleanup` and the lease reaper release whatever is left. Migration `0006`
  adds the reserved columns.
- **Silent worker death.** Workers write heartbeats (`worker_heartbeats`, migration `0005`); offline
  queues show on the dashboard and in Mira's briefing. `scripts/run-worker.ps1` supervises the worker and
  beat on Windows and restarts them (verified: restart ~6 s after a kill).

## Files changed

Baseline commit → working tree (uncommitted):

**Backend**
- `apps/api/app/services/budget.py` (new) — reserve / settle / release / committed_this_month
- `apps/api/alembic/versions/20261120_0006_budget_reservations.py` (new)
- `apps/api/app/models.py` — reserved columns on Organization, Job, Task
- `apps/api/app/services/router.py` — `ensure_budget` reserves
- `apps/api/app/agents/base.py` — current-task context, settle in `record_usage`, release in `cleanup`
- `apps/api/app/services/orchestrator.py` — reaper releases reservations of expired tasks
- `apps/api/app/api/jobs.py` — draft gating, preview endpoint, admin-only approve, committed monthly spend
- `apps/api/app/api/assistant.py`, `apps/api/app/api/posters.py` — monthly checks include reservations
- `apps/api/app/schemas.py` — `released`, optional `download_url`, reviewer-aware `artifact_out`
- `apps/api/app/services/media.py` — `run_ffmpeg` with heartbeat and deadline
- `apps/api/app/agents/video.py` — final render heartbeats
- `apps/api/tests/test_workforce.py` — role, gating, preview, FFmpeg tests; existing tests updated for auth
- `apps/api/tests/test_concurrency.py` (new)

(Worker heartbeats, migration `0005`, `assets.py` admin-only delete, `run-worker.ps1`, CI and the
Postgres test switch landed in the Phase 0 baseline commit.)

**Frontend**
- `apps/web/src/components/ApprovalPanel.tsx` (new, extracted from `JobMonitor.tsx`) and its test
- `apps/web/src/app/(app)/jobs/[id]/JobMonitor.tsx`
- `apps/web/src/lib/intent.ts` — `confirmAction`; `src/app/(app)/assistant/page.tsx` uses it
- `apps/web/src/lib/api.ts` — previews go through the signed-in proxy
- `apps/web/src/app/api/proxy/[...path]/route.ts` — range + streaming
- `apps/web/src/lib/intent.test.ts`, `src/lib/security.test.ts`
- `apps/web/vitest.config.mts`, `package.json`, `package-lock.json` — Testing Library + jsdom

**Infrastructure**
- `compose.integration.yaml` (new) — localhost-only ports for Postgres/Redis/MinIO; stack on 18000/13000/15678
- `scripts/integration-check.ps1` (new)

## Test results

| Suite | Baseline (`2bcc81b`) | Now |
|---|---|---|
| Backend pytest (SQLite) | 83 passed | **97 passed** |
| Backend concurrency (part of the above) | – | 8 passed |
| ruff / mypy | clean | clean |
| `alembic check` (SQLite dev DB at `0006`) | no drift | no drift |
| Frontend Vitest | 18 passed | **27 passed** |
| Frontend lint / typecheck / build | pass | pass |
| `npm audit` | 0 vulnerabilities | 0 vulnerabilities |
| PostgreSQL / Redis / MinIO integration | not run | **not run** (no Docker) |
| n8n end-to-end | not run | **not run** |

Live checks on the dev stack: draft preview renders in the dashboard; Range through the proxy returns
`206`; the approval panel renders on a waiting job; all worker queues report heartbeats;
`/health/ready` reports database, broker and storage `ok`.

Concurrency tests on SQLite serialise writes, so they prove the logic but not PostgreSQL row locking.
They must be re-run with `TEST_DATABASE_URL` pointing at PostgreSQL (the integration script and the
`api-postgres` CI job do this).

## Remaining risks

1. **No integration run yet** on PostgreSQL, Redis, MinIO or n8n (items 6, 7, 10).
2. **Row locking only proven on SQLite.** `SELECT ... FOR UPDATE`-style behaviour of the reservation
   and claim code is unverified on PostgreSQL until the integration run.
3. **Reservations cover agent calls only.** Direct paid calls from API routes (e.g. some poster
   re-renders) still use the pre-check; they are bounded by the monthly check but not reserved.
4. **Windows thread pool has no hard time limits.** Celery `-P threads` ignores `time_limit`; FFmpeg
   has its own deadline, but a hung Python call in another agent relies on the lease reaper.
5. **Estimates, not quotes.** Reservations use cost estimates; a provider that bills more than the
   estimate can overshoot a budget by the difference on that one call.
6. **Single-host secrets.** `.env` is generated locally; there is no secret manager or rotation.
7. **Dev servers in use.** The current machine runs `uvicorn --reload` / `next dev`; not a production
   configuration.

## Deployment instructions

### Local integration run (needed before any production claim)

1. Install Docker Desktop (WSL 2 backend) and start it.
2. From the repository root:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\integration-check.ps1
   ```

   It creates `.env` with random secrets if missing (never committed), starts Postgres/Redis/MinIO,
   runs `alembic upgrade head` via the `migrate` service, starts the API, workers, web and n8n,
   checks `alembic current`/`check` and health, then runs the full backend suite on PostgreSQL
   (`mcr_test` database). Results go to `integration-results.log` (git-ignored).
3. n8n: open http://localhost:15678, import `n8n/workflows/mcr-ai-workforce.json`, create the API key
   (`docker compose exec api python -m app.cli create-api-key n8n`) and the credentials as in
   `docs/N8N.md`, then trigger `http://localhost:15678/webhook/mcr-ai-workforce`. Expect the job to wait
   for approval and the workflow to receive no draft download links.

### Production stack

- `docker compose -f compose.yaml up -d --build` (no integration overlay). Run migrations with the
  `migrate` service before starting the API and workers.
- Set strong values for `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `JWT_SECRET`,
  `SECRETS_ENCRYPTION_KEY` (Fernet), `N8N_ENCRYPTION_KEY`; keep `.env` out of git.
- Put the web and API behind HTTPS; do not expose Postgres, Redis or MinIO ports publicly.
- Run Linux workers with the prefork pool so Celery time limits apply.

### Windows dev machine (current setup)

- API: `uv run uvicorn app.main:app --port 8000` in `apps/api`
- Web: `npm run dev` in `apps/web`
- Workers: `powershell -ExecutionPolicy Bypass -File scripts\run-worker.ps1`
