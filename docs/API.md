# MCR AI Workforce — REST API

Base URL (dev): `http://localhost:8000`. Interactive OpenAPI docs: `/docs` (Swagger) and `/openapi.json`.

## Authentication

Every `/v1/*` endpoint except `POST /v1/auth/login` and signed artifact downloads requires one of:

- `Authorization: Bearer <jwt>` — obtained from `POST /v1/auth/login` (dashboard users).
- `X-API-Key: <key>` — organization service key (n8n, scripts). Created with `python -m app.cli create-api-key`.

All data is scoped to the caller's organization. Records of other organizations return `404`.

## Error format

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "human readable", "details": {} } }
```

Common codes: `UNAUTHENTICATED` (401), `FORBIDDEN` (403), `NOT_FOUND` (404), `VALIDATION_ERROR` (422),
`CONFLICT` (409), `RATE_LIMITED` (429), `BUDGET_EXCEEDED` (402), `INTERNAL_ERROR` (500).

## Auth

### `POST /v1/auth/login`
Body: `{ "email": "...", "password": "..." }`

```json
{
  "access_token": "<jwt>", "token_type": "bearer", "expires_in": 43200,
  "user": { "id": "uuid", "email": "...", "full_name": "..." },
  "organization": { "id": "uuid", "name": "...", "slug": "..." },
  "role": "owner"
}
```

### `POST /v1/auth/change-password` → `204`
Body: `{ "current_password": "...", "new_password": "10..128 chars" }`. Signed-in users only (not API keys).
`400 WRONG_PASSWORD` if the current password is incorrect; `422` if the new password is too short, equal
to the current one, or starts/ends with a space. Rate limited like login. Existing sessions stay valid
until their token expires.

### `GET /v1/auth/me`
Returns `{ "user": {...}, "organization": {...}, "role": "owner|admin|member" }`.

## Jobs

### Job object

```json
{
  "id": "uuid", "organization_id": "uuid", "project_id": "uuid",
  "command": "Create a promotional campaign ...",
  "status": "queued|planning|planned|running|awaiting_approval|completed|partial|failed|cancelling|cancelled|rejected",
  "demo_mode": false,
  "planner_mode": "llm|rules|null",
  "language": "th|en",
  "aspect_ratio": "16:9|9:16",
  "data_classification": "public|internal|restricted",
  "requested_agents": ["content", "image", "document", "video"],
  "budget_limit_usd": 5.0,
  "estimated_cost_usd": 0.42,
  "actual_cost_usd": 0.13,
  "progress": 60,
  "task_counts": { "pending": 0, "queued": 0, "running": 1, "succeeded": 3, "failed": 0, "skipped": 0, "cancelled": 0 },
  "qa_verdict": "PASS|REVISE|HUMAN_REVIEW|null",
  "qa_summary": "string|null",
  "require_approval": true,
  "approval_status": "pending|approved|rejected|null",
  "error_code": null, "error_message": null,
  "created_at": "ISO-8601", "started_at": "ISO-8601|null", "finished_at": "ISO-8601|null", "updated_at": "ISO-8601"
}
```

Status meaning:

| status | meaning |
|---|---|
| `queued` | accepted and persisted, waiting for the planner |
| `planning` | planner agent is building the task graph |
| `planned` | plan ready; waiting for dispatch (only when `auto_start=false`) |
| `running` | tasks are executing |
| `awaiting_approval` | all work finished, a human must approve (required, or QA returned `HUMAN_REVIEW`) |
| `completed` | every required deliverable was generated and QA passed (and approved if required) |
| `partial` | some deliverables succeeded, at least one failed |
| `failed` | nothing usable was produced or planning failed |
| `cancelling` / `cancelled` | cancellation requested / finished |
| `rejected` | a human rejected the deliverables |

`demo_mode=true` means at least one deliverable was produced by an explicit demo fallback (no AI provider
configured). Demo artifacts carry `is_demo=true` and must not be presented as AI-generated work.

### `POST /v1/jobs` → `202`
Headers: optional `Idempotency-Key: <string ≤ 128>`. Re-sending the same key for the same organization returns
the original job with `200` instead of creating a new one.

```json
{
  "command": "5..4000 chars (required)",
  "agents": ["content", "image", "document", "video"],
  "budget_limit_usd": 5.0,
  "language": "th",
  "aspect_ratio": "16:9",
  "require_approval": true,
  "data_classification": "internal",
  "project_id": null,
  "auto_start": true
}
```
All fields except `command` are optional. Returns the Job object.

**Event posters:** add `"poster": {...}` and the job makes only 9:16 poster(s) (`agents` and `aspect_ratio` are
ignored). The image provider paints only the artwork; the title, date banner, headline, venue, time and logos are
typeset by the app, so Thai text is always exact and logos are never redrawn.

```json
"poster": {
  "theme": "gold | neon | crimson",
  "tag": "ROV", "title": "Valor City (required, ≤ 40)", "edition": "2026",
  "date_text": "วันอาทิตย์ที่ 3 พฤษภาคม 2569",
  "headline": ["ภาคใต้ตอนบน รอบ 8 ทีมสุดท้าย", "สายแข่งศูนย์สุราษฎร์ธานี"],
  "venue": "…", "time_text": "10.00 น. เป็นต้นไป",
  "art": "description of an original character / scene (≤ 400)",
  "event_logo_id": null, "sponsor_logo_ids": [], "presenter_label": "Presented by", "presenter_logo_ids": [],
  "variants": 1
}
```
Logo ids come from the logo library below (`422 ASSET_NOT_FOUND` otherwise). Up to 3 headline lines (≤ 48
chars each), 8 sponsor and 6 presenter logos, 1–3 variants. "New image" on a poster paints new artwork and
re-typesets the same text.

### `GET /v1/jobs?status=&limit=20&offset=0`
`{ "items": [Job], "total": 42, "limit": 20, "offset": 0 }` — newest first. `status` may be repeated.

### `GET /v1/jobs/{job_id}`
Job object plus `"plan": { ... } | null` (the validated planner output).

### `GET /v1/jobs/{job_id}/tasks`
```json
{ "items": [ {
  "id": "uuid", "job_id": "uuid", "key": "content_captions", "agent": "planner|content|image|document|video|qa",
  "title": "5 Thai Facebook captions",
  "status": "pending|queued|running|succeeded|failed|skipped|cancelled",
  "provider": "openai|comfyui|local_cpu|ffmpeg|rules|demo|null",
  "execution_location": "cloud|local|null",
  "routing_reason": "string|null",
  "attempt": 1, "max_attempts": 3,
  "depends_on": ["content_captions"],
  "params": {},
  "error_code": null, "error_message": null,
  "estimated_cost_usd": 0.01, "actual_cost_usd": 0.004,
  "queued_at": null, "started_at": null, "finished_at": null
} ] }
```

### `GET /v1/jobs/{job_id}/artifacts`
```json
{ "items": [ {
  "id": "uuid", "job_id": "uuid", "task_id": "uuid", "task_key": "image_1", "agent": "image",
  "kind": "image|caption|copy|document_pdf|document_docx|video|subtitle|audio|script|storyboard|manifest|qa_report",
  "filename": "promo-1.png", "content_type": "image/png", "size_bytes": 12345, "sha256": "hex",
  "is_demo": false,
  "text_preview": "first 4000 chars for text artifacts, else null",
  "metadata": { "width": 1920, "height": 1080, "license": "generated", "provider": "comfyui" },
  "download_url": "/v1/artifacts/<id>/download?exp=...&sig=...",
  "created_at": "ISO-8601"
} ] }
```
`download_url` is a relative, HMAC-signed, expiring URL (default 15 minutes) served by the API. It needs no
auth header, so it can be used directly in `<img>`/`<video>` tags. Supports HTTP `Range`.

### `GET /v1/artifacts?kind=&limit=20&offset=0`
Recent artifacts across all jobs of the organization. Same item shape, paginated like jobs.

### `PUT /v1/artifacts/{artifact_id}/text`
Body: `{ "text": "1..20000 chars" }`. Replaces the content of a plain-text deliverable (caption, copy text,
storyboard, subtitles) and returns the updated artifact with a new checksum and `metadata.edited = true`.
`409` for other file types (images, PDFs, JSON…) or while the job is still active. The job's timeline gets an
`artifact.edited` entry with the old and new size (never the text).

### `POST /v1/artifacts/{artifact_id}/regenerate` → `202`
Body (optional): `{ "concept": "new scene description" }` for images, `{ "instruction": "what to change" }` for
documents (each up to 500 chars). Queues a new version on the agent's worker: images via local ComfyUI first,
then the authorised cloud fallback; documents are rewritten and re-rendered, and their PDF and DOCX are replaced
together; videos (requested on the MP4) get a new script and are re-rendered, replacing the MP4, script, storyboard,
subtitles, scene manifest and voiceover together. `instruction` applies to documents and videos. When it succeeds the new image
replaces the old one under the same task; if it fails, the old image is kept and its `metadata.regenerate_error`
explains why. While it runs, the old image's `metadata.regenerating_since` is set. After a successful swap
QA runs again for the whole job and replaces the QA report and verdict (a failed check becomes `HUMAN_REVIEW`;
the finished job is never reworked automatically). `409` for non-images, while
the job is active, or while another regeneration of the same image is running. The dashboard polls
`GET /v1/jobs/{job_id}/artifacts` to see the result.

### `POST /v1/artifacts/{artifact_id}/regenerate-caption` → `200`
Body (optional): `{ "instruction": "e.g. shorter, more playful (up to 500 chars)" }`. Rewrites one caption with
the cloud model, using the verified business context and avoiding the job's other captions, and replaces the
caption file's content. Returns `{ "artifact": Artifact, "warnings": ["unsupported claim …"] }`. QA for the job is
then re-run in the background. `409` for non-captions, while the job is active, or without a configured provider;
`402` when the budget or provider credit is exhausted.

### `DELETE /v1/artifacts?kind=` (owner/admin)
Permanently deletes the organization's stored deliverables (optionally one `kind`). Jobs are kept.
Files of jobs that are still active are skipped. Returns `{ "deleted": 40, "skipped_active": 0 }`.

### `GET /v1/jobs/{job_id}/events`
Execution timeline: `{ "items": [ { "id": "uuid", "at": "ISO-8601", "action": "task.succeeded", "actor": "worker|user:<email>|system|n8n", "task_key": "image_1|null", "detail": {} } ] }` oldest first.

### `POST /v1/jobs/{job_id}/cancel`
Returns the Job (`cancelling` or `cancelled`). `409` if the job is already terminal.

### `POST /v1/jobs/{job_id}/approve`
Body: `{ "decision": "approved|rejected", "comment": "optional" }`. Only valid in `awaiting_approval` (`409` otherwise).
Returns the Job (`completed`/`partial` or `rejected`).

### `DELETE /v1/jobs?status=` (owner/admin)
Permanently deletes every job of the organization that is no longer active, with its files. `status`
may be repeated to delete only those statuses. Active jobs are skipped.
Returns `{ "deleted": 12, "skipped_active": 1, "files_removed": 87 }`.

### `DELETE /v1/jobs/{job_id}` (owner/admin) → `204`
Permanently deletes a job that is no longer active, together with its tasks, stored files, approvals
and timeline. `409` while the job is `queued`, `planning`, `planned`, `running` or `cancelling`
(cancel it first). Usage events are kept so spending history and budgets stay correct, and a
`job.deleted` entry is written to the organization audit log.

### Orchestration endpoints (used by n8n when `auto_start=false`)
- `POST /v1/jobs/{job_id}/plan` → `202` Job. Enqueues the planner (idempotent).
- `POST /v1/jobs/{job_id}/dispatch` → `202` `{ "job": Job, "dispatched": ["content_captions", "image_1"] }`. Enqueues every ready task onto its Celery queue (idempotent).
- `GET /v1/jobs/{job_id}/qa` → `{ "verdict": "PASS|REVISE|HUMAN_REVIEW|null", "summary": "...", "checks": [ { "name": "...", "status": "pass|fail|warn", "detail": "..." } ] }`
- `POST /v1/jobs/{job_id}/events` body `{ "action": "n8n.<name>", "detail": {} }` → `201`. Appends an external event to the audit trail.

## Poster design chat

### `POST /v1/posters/chat`
Body: `{ "messages": [{"role": "user"|"assistant", "content": "..."}], "draft": {…current poster form…}, "language": "th" }`
(up to 30 messages). The assistant (OpenAI chat model) returns `{ reply, draft, ready, unknown_assets }`: `draft` is the
whole updated poster form. Logos and artwork are referenced by library name and mapped to ids on the server
(names not in the library are dropped and listed in `unknown_assets`). Fields the model leaves out keep their
current value. Nothing is generated or created here; it costs one small chat call (recorded as agent `assistant`).
`409 PROVIDER_NOT_CONFIGURED` without OpenAI. The Assistant page hands poster requests to this chat.

## Logo library

### `POST /v1/assets` → `201`
Multipart form: `file` (PNG, JPEG or WebP, ≤ 8 MB) and optional `name`. The image is decoded and re-encoded as PNG
(transparency kept, at most 2000 px per side). Returns `{id, name, width, height, size_bytes, url, created_at}`;
`url` is a signed, expiring link (`/v1/assets/{id}/file?exp=&sig=`). At most 200 logos per organization.

### `GET /v1/assets`
The organization's logos in upload order.

### `DELETE /v1/assets/{asset_id}` → `204`
Posters already made keep the logo (it is part of the image). A poster job that runs after its logo was deleted
leaves it out and QA warns (`poster:logos`).

## Dashboard

### `GET /v1/dashboard/summary`
```json
{ "active_jobs": 2, "completed_jobs": 10, "failed_jobs": 1, "awaiting_approval_jobs": 1,
  "month_cost_usd": 3.21, "active_estimated_cost_usd": 0.8, "monthly_budget_usd": 50.0,
  "recent_artifacts": [Artifact] }
```

## Usage

### `GET /v1/usage?from=YYYY-MM-DD&to=YYYY-MM-DD`
```json
{ "period": { "from": "2026-11-01", "to": "2026-11-30" },
  "total_cost_usd": 3.21, "monthly_budget_usd": 50.0,
  "by_provider": [ { "provider": "openai", "cost_usd": 3.0, "events": 41 } ],
  "by_agent": [ { "agent": "content", "cost_usd": 0.4, "events": 12 } ],
  "daily": [ { "date": "2026-11-18", "cost_usd": 0.6 } ] }
```
Defaults to the current calendar month (UTC).

## Settings

### `GET /v1/settings`
```json
{ "organization": { "id": "uuid", "name": "...", "slug": "...", "monthly_budget_usd": 50.0,
                    "default_job_budget_usd": 5.0, "allow_cloud_fallback": true,
                    "allow_external_ai_for_restricted": false, "business_context": "verified facts text" },
  "providers": [ { "name": "openai", "kind": "llm|image|tts", "location": "cloud|local",
                   "configured": true, "enabled": true, "healthy": true, "detail": "model gpt-…" } ],
  "workers": [ { "queue": "planner", "online": true, "workers": 1 } ],
  "demo_mode_enabled": true }
```
Secrets are never returned.

### `PATCH /v1/settings/organization` (owner/admin)
Any subset of: `name`, `monthly_budget_usd`, `default_job_budget_usd`, `allow_cloud_fallback`,
`allow_external_ai_for_restricted`, `business_context`. Returns the organization object.

### `PUT /v1/settings/providers/{name}` (owner/admin)
`name` ∈ `openai`, `comfyui`, `firefly`. Body: any subset of `{ "enabled": true, "api_key": "...", "base_url": "...", "model": "..." }`.
The key is encrypted at rest (Fernet) and never echoed. Returns the provider object.

For `firefly` (Adobe Firefly Services): `api_key` is the **Client Secret**, plus `client_id`, `model` (blank,
`image3`, `image4_standard` or `image4_ultra`), `content_class` (`art` | `photo`) and `priority`
(`fallback`: after local ComfyUI, the default; `first`: before ComfyUI). The provider object's `options` show the
non-secret values (client id masked). The health check signs in to Adobe (free; no image is generated).
Images use the async Generate Image API (v3): the access token is cached until shortly before it expires and is
only ever sent to the Firefly API host; the finished image is fetched from Adobe's pre-signed URL without
credentials. Restricted data never goes to Firefly unless the organization allows external AI for it.

## Assistant voice

### `POST /v1/assistant/speech`
Body: `{ "text": "1..600 chars", "language": "th|en" }`. Returns `audio/mpeg` (MP3) synthesized by the
configured cloud speech provider with the voice `ASSISTANT_TTS_VOICE` (default `nova`). The cost is
recorded as a usage event with agent `assistant` and counts toward the monthly budget.
`409 PROVIDER_NOT_CONFIGURED` when no provider is configured (the dashboard then uses the browser
voice), `402 BUDGET_EXCEEDED` when the monthly budget is exhausted, `502` on provider errors.
The text is sent to the external speech provider.

## Health
- `GET /health/live` → `200 {"status":"ok"}` (process is up)
- `GET /health/ready` → `200`/`503` `{"status":"ok|degraded","checks":{"database":"ok","broker":"ok","storage":"ok"}}`
