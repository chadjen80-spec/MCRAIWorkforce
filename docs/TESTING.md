# Testing

## Automated checks

Backend (`apps/api`):

```powershell
cd apps\api
uv run pytest -q          # 83 tests (SQLite)
# the same suite on PostgreSQL (CI does this with a postgres:17 service):
# $env:TEST_DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/mcr_test"; uv run pytest -q
uv run ruff check .       # lint
uv run mypy app           # type check
uv run alembic upgrade head ; uv run alembic check   # migration applies and matches the models
```

Dashboard (`apps/web`):

```powershell
cd apps\web
npm run lint
npm run typecheck
npm test          # Vitest unit tests for pure logic (src/**/*.test.ts)
npm run build
```

Continuous integration (`.github/workflows/ci.yml`) runs all of the above on every push and pull
request, plus the backend suite and `alembic upgrade/downgrade/upgrade` against PostgreSQL 17 with
Redis 7.

The backend tests run the whole pipeline in-process (Celery eager mode, SQLite, local storage). AI
providers are replaced at the HTTP transport level, so the real adapters, router, agents, renderers
and FFmpeg are exercised. They cover:

- rule-based planning of the acceptance command (English) and a Thai command;
- the acceptance command end to end in explicit demo mode: task graph and dependencies, 3 PNG
  images, 5 captions, a PDF with an embedded font, a 1920×1080 H.264/AAC 60 s MP4, signed download
  with HTTP Range, QA escalation to human review, approval;
- no provider and demo mode off → every AI task fails with `PROVIDER_NOT_CONFIGURED`, the job is
  `failed`, nothing is reported as generated;
- a full run with a cloud LLM and local ComfyUI (faked at HTTP level): QA `PASS`, `completed`,
  voiceover muxed, usage and cost recorded;
- routing: local preferred, cloud fallback when ComfyUI is down or saturated, fallback refused when
  the organization disallows it, restricted data never sent to an external provider;
- retries with backoff, bounded retries and dead-lettering, budget rejection at planning;
- QA-requested revision when a stored file goes missing, rejection when revisions are exhausted,
  partial success when one agent fails;
- idempotent job creation, the n8n-driven plan → dispatch flow, cancellation (queued and running),
  lease-expiry recovery by the reaper, duplicate message delivery;
- tenant isolation, authentication, validation, login rate limiting, signed-URL tampering and expiry,
  encrypted provider secrets, health endpoints, refusal of unsafe production settings.

## End-to-end acceptance check against a running stack

Start the stack ([INSTALL_WINDOWS.md](INSTALL_WINDOWS.md)), then:

```powershell
cd apps\api
uv run python ..\..\scripts\e2e_acceptance.py --approve
```

For the Docker stack pass `--api http://localhost:8000` and set `E2E_EMAIL` / `E2E_PASSWORD`.

The script submits the acceptance command, polls with a new connection each time (as a browser
reopened later would), downloads every artifact through its signed URL, verifies size, SHA-256 and
file signatures, checks the expected counts (3 images, 5 captions, 1 PDF, 1 MP4) and prints the QA
verdict. It exits non-zero if anything is missing. It reports `DEMO MODE` when the deliverables are
placeholders.

Manual checks worth doing in the dashboard:

1. Workforce → "Use example" → Execute. You land on the Job Monitor.
2. Close the browser tab while the job runs; reopen `/jobs` later: the job has progressed.
3. Deliverables: image previews, caption text, PDF download, video playback (seek works).
4. Approve or reject; the job becomes `completed` or `rejected`.
5. Stop the worker, submit a job (it stays `queued`), start the worker: the job runs.

## Results recorded on the development machine (2026-11-18, Windows 11, no Docker)

| Check | Result |
|---|---|
| `pytest` | 28 passed |
| `ruff check`, `mypy app` | clean |
| `alembic upgrade head` + `alembic check` on SQLite | applied, no drift |
| Dashboard `lint`, `typecheck` | clean |
| Dashboard `build` | passed (run by the dashboard build step, not repeated after integration) |
| Live run: uvicorn + real Celery worker (filesystem broker) + SQLite + local storage, demo mode | acceptance script passed: 8/8 tasks, 17 files verified, 60.0 s 1920×1080 h264/aac MP4, QA `HUMAN_REVIEW` (demo), approved → `completed` |
| Concurrency in that run | content and 3 image tasks started within 60 ms of each other and overlapped; document and video started only after their dependencies; QA last |
| Durability in that run | job submitted while no worker was running stayed `queued`; completed after the worker was started |
| Celery beat + reaper | ran live; reaper task executed on schedule |
| Dashboard against the live API | login, dashboard, job monitor, deliverables (video player), Thai/English, 375 px layout checked in a browser |

Update 2026-11-20 (Phase 0): `pytest` 83 passed, `ruff` and `mypy` clean; dashboard `lint`,
`typecheck`, `npm test` (18 tests) and `build` passed. The PostgreSQL CI job is written but has not
run yet (the repository has no remote).

Not run on that machine: anything requiring Docker, PostgreSQL, Redis, MinIO, a real OpenAI key, a
real ComfyUI instance, or a running n8n. See [LIMITATIONS.md](LIMITATIONS.md).
