# n8n workflow

File: `n8n/workflows/mcr-ai-workforce.json` (regenerate with `python n8n/build_workflow.py`).
It supersedes the earlier `MCR_AI_Workforce_n8n_DEMO.json`, whose four agent nodes returned
hard-coded sample output; every agent step now reflects real queue-backed work.

## What the workflow does

| # | Node(s) | Action |
|---|---|---|
| 1 | Webhook | `POST /webhook/mcr-ai-workforce`, protected by a Header Auth credential |
| 2 | Validate Input, Input Valid? | Checks command length, agents, budget; `400` on bad input |
| 3 | Submit Job | `POST /v1/jobs` with `auto_start=false` and an `Idempotency-Key` |
| – | Respond 202 | Returns `{ job_id, status }` immediately; the caller never waits for rendering |
| 4 | Invoke Planner, Wait For Plan, Get Plan Status, Planned? | `POST /v1/jobs/{id}/plan`, then polls every 3 s |
| 5 | Dispatch Tasks | `POST /v1/jobs/{id}/dispatch`: every ready task goes onto its Celery queue |
| 6–9 | Content / Image / Document / Video Agent | Read each agent's task states from `GET /v1/jobs/{id}/tasks` |
| – | Merge Agent Status, Get Job, Job Finished? | Polls every 10 s until the job leaves the active states |
| 10 | QA Coordination | `GET /v1/jobs/{id}/qa` (verdict and checks) |
| 11 | Job Status Update | `POST /v1/jobs/{id}/events` writes `n8n.workflow_completed` to the job's audit trail |
| 12 | Completion Notification | `POST` to `MCR_NOTIFY_WEBHOOK_URL` when it is set |

Parallelism comes from the Celery queues, not from n8n branches: the four agent nodes only observe.
Dependent tasks (document, video, QA) are released by the API as their prerequisites finish.
Every HTTP node has a timeout (15 s, 10 s for notifications), three tries and an error output that
leads to a failure notification. The monitor loop gives up after 360 polls (about one hour); the job
itself continues and stays visible in the dashboard.

## Import and credentials

The n8n key is a **service** key: it can submit, plan, dispatch and follow jobs and write events, but
it cannot approve deliverables, delete anything or change settings (those return 403). Approval stays
with a signed-in owner or admin, and n8n receives download links only after a job is approved.

No API key or password is stored in the JSON. The nodes refer to two credentials by name; create
them in n8n and select them once after import.

1. Create an API key for n8n (shown once):
   ```
   docker compose exec api python -m app.cli create-api-key n8n
   ```
   (without Docker: `uv run python -m app.cli create-api-key n8n` in `apps/api`)
2. In n8n → Credentials → New → **Header Auth**, name it `MCR API Key`:
   Name `X-API-Key`, Value = the key from step 1.
3. Create a second **Header Auth** credential named `MCR Webhook Auth`:
   Name `X-Webhook-Token`, Value = a long random string of your own. Callers of the webhook must send
   this header.
4. Workflows → Import from File → `n8n/workflows/mcr-ai-workforce.json`
   (in the Compose stack the folder is mounted read-only at `/workflows`).
5. Open the **Webhook** node and choose `MCR Webhook Auth`; open each HTTP Request node that calls the
   MCR API and choose `MCR API Key`.
6. Activate the workflow.

Environment the n8n container needs (already set in `compose.yaml`):

- `MCR_API_URL` — internal API address, `http://api:8000`
- `MCR_NOTIFY_WEBHOOK_URL` — optional incoming-webhook URL for notifications
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` — lets expressions read the two variables above
- `N8N_ENCRYPTION_KEY` — encrypts the credentials n8n stores

## Calling it

```
curl.exe -X POST http://localhost:5678/webhook/mcr-ai-workforce ^
  -H "X-Webhook-Token: <your token>" -H "Content-Type: application/json" ^
  -d "{\"command\": \"Create 3 promotional images and 5 Thai Facebook captions for MCR SME Copilot\", \"request_id\": \"order-1001\"}"
```

Optional body fields: `agents`, `budget_limit_usd`, `language`, `aspect_ratio`, `require_approval`,
`data_classification`, `request_id` (used for idempotency; repeated calls with the same value return
the same job).
