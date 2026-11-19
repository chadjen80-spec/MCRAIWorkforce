# Production deployment checklist

Deploy with both Compose files and a dedicated env file that is not in the repository:

```
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml up -d --build
```

## Before the first deploy

- [ ] Linux host (or VM) with Docker Engine; ports 80 and 443 open; nothing else open to the internet.
- [ ] DNS A/AAAA records for `APP_DOMAIN` and `N8N_DOMAIN` point to the host. Caddy obtains and renews
      HTTPS certificates automatically; HTTP is redirected to HTTPS.
- [ ] `.env.prod` created from `.env.example` with `ENVIRONMENT=production`, `APP_VERSION`,
      `APP_DOMAIN`, `N8N_DOMAIN`, `N8N_PROTOCOL=https`, `N8N_WEBHOOK_URL`, `CORS_ORIGINS=https://<APP_DOMAIN>`,
      `SESSION_COOKIE_SECURE=true`.
- [ ] Secrets are freshly generated, unique to production, and stored in a secret manager (or at
      minimum a root-only file, `chmod 600`): `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `JWT_SECRET`
      (≥ 32 chars), `SECRETS_ENCRYPTION_KEY`, `N8N_ENCRYPTION_KEY`.
      Losing `SECRETS_ENCRYPTION_KEY` makes stored provider keys unreadable; losing
      `N8N_ENCRYPTION_KEY` makes n8n credentials unreadable. Back both up separately from the data.
- [ ] Every third-party image is pinned to an exact tag or digest that you have pulled and tested:
      `POSTGRES_IMAGE`, `REDIS_IMAGE`, `MINIO_IMAGE`, `N8N_IMAGE`, `CADDY_IMAGE`. The deploy aborts if
      one is missing. Also pin the base images in the two Dockerfiles by digest.
- [ ] `DEMO_MODE` is absent or `false`. (With `ENVIRONMENT=production` the API additionally refuses
      weak `JWT_SECRET`, a missing encryption key, SQLite, the filesystem broker and eager tasks.)
- [ ] The price table (`LLM_INPUT_USD_PER_1M`, `LLM_OUTPUT_USD_PER_1M`, `IMAGE_USD_PER_IMAGE`,
      `TTS_USD_PER_1K_CHARS`) matches your provider's current prices; budgets are only as accurate
      as this table.
- [ ] Create the administrator once, then remove `BOOTSTRAP_ADMIN_PASSWORD` from the env file:
      `docker compose ... run --rm api python -m app.cli seed`
- [ ] Create the n8n API key and credentials ([N8N.md](N8N.md)); set an owner account on n8n.
- [ ] Set organization budgets and the verified business context in Settings.

## Network and exposure

- [ ] Only Caddy publishes ports. `api`, `web` and `n8n` bind to `127.0.0.1`; PostgreSQL, Redis and
      MinIO publish nothing and are on the internal `backend` network.
- [ ] ComfyUI is reachable only from the worker host (never from the internet); it has no authentication.
- [ ] Host firewall allows inbound 80/443 (and SSH from trusted addresses) only.
- [ ] If more than one API replica runs, keep `REDIS_URL` set so rate limits are shared.

## After deploy

- [ ] `docker compose ... ps` shows every service healthy; `migrate` exited with code 0.
- [ ] `GET /health/ready` (from the host: `curl http://127.0.0.1:8000/health/ready`) returns `ok` for
      database, broker and storage.
- [ ] Run `scripts/e2e_acceptance.py` against the deployment with real providers configured and
      confirm `demo_mode=false`.
- [ ] Sign in over HTTPS, submit a job, close the browser, reopen and confirm the job continued.
- [ ] Kill a worker container mid-job (`docker kill`) and confirm the task is re-queued within the
      lease time (5 minutes by default) and the job still finishes.

## Backup and recovery

What must be backed up: the PostgreSQL database (jobs, tasks, audit log, usage, encrypted provider
keys), the MinIO bucket (deliverables), the n8n volume (workflows, credentials) and the two
encryption keys. Redis holds only in-flight task messages and does not need backups: after a Redis
loss the lease reaper re-sends every queued or running task from PostgreSQL.

Database (run daily from cron, keep off-host copies):

```
docker compose -f compose.yaml -f compose.prod.yaml exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > backup/mcr-$(date +%F).dump
```

Restore into an empty database:

```
docker compose ... stop api worker-core worker-image worker-video worker-maintenance web n8n
docker compose ... exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backup/mcr-YYYY-MM-DD.dump
docker compose ... up -d
```

Artifacts: mirror the bucket with the MinIO client to another location, for example
`mc mirror --overwrite local/mcr-artifacts backup-target/mcr-artifacts`, or snapshot the
`minio-data` volume. Restore by mirroring back before starting the API.

n8n: snapshot the `n8n-data` volume, or export with `n8n export:workflow --all` and
`n8n export:credentials --all` (the credential export is encrypted with `N8N_ENCRYPTION_KEY`).

- [ ] Restore has been rehearsed at least once on a separate machine.

## Upgrades

1. Back up the database.
2. Build the new images with a new `APP_VERSION`.
3. `docker compose ... up -d`: `migrate` runs `alembic upgrade head` before the API and workers start.
4. Roll back by redeploying the previous `APP_VERSION` and, if a migration was applied,
   `alembic downgrade -1` or restoring the backup.

## Monitoring

- All services log structured JSON to stdout (request id, job id, task key, agent, attempt, error
  code). Ship container logs to your log system and alert on `task failed`, `task crashed` and
  `reaper recovered tasks`.
- Poll `/health/ready` from an uptime monitor on the host.
- The audit trail (`GET /v1/jobs/{id}/events`, table `audit_logs`) records every state change,
  retry, dead-lettered task, approval and settings change.
- Watch `GET /v1/usage` against the monthly budget.
