# Installation on Windows 11

Commands are for PowerShell, run from the repository root (`C:\MCRAIWorkforce`) unless stated.

## Option A — without Docker (single machine)

Uses SQLite, a filesystem Celery broker and local file storage. Same application code as the full
stack; intended for development and evaluation, not for production.

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node.js 20 or newer. Python and FFmpeg are
installed automatically by `uv sync` (FFmpeg comes from the `imageio-ffmpeg` package).

1. Install backend dependencies:
   ```powershell
   cd apps\api
   uv sync
   ```
2. Create `apps\api\.env`. Generate the two secrets with `uv run python -m app.cli generate-secrets`:
   ```
   JWT_SECRET=<generated>
   SECRETS_ENCRYPTION_KEY=<generated>
   BOOTSTRAP_ADMIN_EMAIL=you@example.com
   BOOTSTRAP_ADMIN_PASSWORD=<at least 10 characters>
   LOG_JSON=false
   # Optional providers:
   # OPENAI_API_KEY=...
   # COMFYUI_URL=http://127.0.0.1:8188
   # Only to try the pipeline without any AI provider (outputs are labelled DEMO):
   # DEMO_MODE=true
   ```
3. Create the database and the first administrator:
   ```powershell
   uv run alembic upgrade head
   uv run python -m app.cli seed
   ```
4. Start the API from `apps\api`, and the background worker from the repository root, each in its own terminal:
   ```powershell
   uv run uvicorn app.main:app --port 8000
   ```
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\run-worker.ps1
   ```
   The script runs the Celery worker (threads pool: Celery's default process pool does not work on
   Windows) and Celery beat (the lease reaper every 30 seconds, which recovers tasks of a crashed
   worker), restarts either one if it stops, and logs to `apps\api\data\worker.log` and `beat.log`.
   Each worker writes a heartbeat to the database every 20 seconds; the dashboard, Settings and Mira
   warn when no worker has reported for 75 seconds.
5. Start the dashboard from `apps\web`:
   ```powershell
   npm install
   npm run dev
   ```
6. Open http://localhost:3000 and sign in with the bootstrap e-mail and password.
   API documentation: http://localhost:8000/docs.

Data lives in `apps\api\data\` (database, broker queue, artifacts). Delete that folder to start over.

## Option B — Docker Desktop (full stack)

Prerequisites: Docker Desktop with the WSL 2 backend.

1. `Copy-Item .env.example .env` and fill in every value marked required. Generate
   `JWT_SECRET` and `SECRETS_ENCRYPTION_KEY` as in option A (or with any password generator for the
   others).
2. Build and start:
   ```powershell
   docker compose up -d --build
   ```
   The `migrate` service applies the Alembic migrations and creates the administrator from
   `BOOTSTRAP_ADMIN_*`.
3. Check health:
   ```powershell
   docker compose ps
   curl.exe http://localhost:8000/health/ready
   ```
4. Open:
   - Dashboard: http://localhost:3000
   - API docs: http://localhost:8000/docs
   - n8n: http://localhost:5678 (see [N8N.md](N8N.md))

   All ports are bound to `127.0.0.1`. PostgreSQL, Redis and MinIO have no published ports and sit on
   an internal Docker network.
5. To use the GPU of the Windows host for images, run ComfyUI on the host and set
   `COMFYUI_URL=http://host.docker.internal:8188` (see [GPU_WORKER.md](GPU_WORKER.md)).

Stop with `docker compose down`. Add `-v` only if you want to delete all data volumes.

## Configuring providers

Either set `OPENAI_API_KEY` / `COMFYUI_URL` in the environment, or sign in as owner/admin and use
**Settings → Providers**. Keys entered in the dashboard are encrypted with `SECRETS_ENCRYPTION_KEY`
before they are stored and are never returned by the API.

Add verified facts about the business in **Settings → Organization → Business context**. The
Content, Document, Video and QA agents are instructed to use only those facts.
