# MCR Smart Café — Architecture

Companion to [MCR_SMART_CAFE_PRD.md](MCR_SMART_CAFE_PRD.md). Describes the target design and marks
what v0.1 implements. Anything not marked **built** is design only.

## 1. Components

```
                  ┌──────────────────────── Cloud (MCR platform) ────────────────────────┐
 Owner / staff ──►│ Next.js apps/web  ─►  FastAPI apps/api  ─►  PostgreSQL (source of truth)│
 browser          │   /cafe/*, Mira       /v1/cafe/*            Redis (rate limit, pub/sub)  │
                  │                       Celery (reports, outbox)  OpenAI (tool calling)    │
                  └──────────────▲──────────────────────────────────▲───────────────────────┘
                                 │ HTTPS / WSS (when online)         │ sync (outbox, both ways)
                  ┌──────────────┴──────── Shop LAN ──────────────────┴──────────────────────┐
                  │ Local store server (design)  ── same API subset, SQLite/PostgreSQL,     │
                  │   signs commands with the shop's key, queues sync                       │
                  │        ▲ WSS (LAN)                                                       │
                  │ Seat PC: MCR Smart Café client (C# .NET, clients/windows)                │
                  │   device identity · heartbeat · command verifier · allowlist launcher   │
                  └──────────────────────────────────────────────────────────────────────────┘
```

v0.1 runs the seat client directly against the cloud API (no local server yet).

## 2. Backend (`apps/api/app/cafe`) — **built** for the slice

| File | Purpose |
|---|---|
| `models.py` | `cafe_seats`, `cafe_devices`, `cafe_enrollment_codes`, `cafe_apps`, `cafe_sessions`, `cafe_commands`, `cafe_assist_requests`, `cafe_audit_events` |
| `flags.py` | `CAFE_ENABLED` and `CAFE_MODULES` checks (`404 FEATURE_DISABLED` when off) |
| `signing.py` | ECDSA P-256 command signing, canonical JSON, server key management |
| `device_auth.py` | Device token (separate secret and `aud`), `get_device` dependency |
| `service.py` | Permission checks, dispatch, result handling, remaining time — shared by HTTP, WebSocket and Mira |
| `api.py` | Staff/admin endpoints (`/v1/cafe/...`) |
| `device_api.py` | Device endpoints (`/v1/cafe/device/...`) and the WebSocket |
| `assistant.py` | Mira's café tools: structured tool calling, executor, replies built from results |

Migration `0012_cafe` creates the tables. Every table has `organization_id`; every query filters on it.

### Feature flags

- `CAFE_ENABLED=false` (default): every `/v1/cafe/*` route answers 404 `FEATURE_DISABLED`; Mira's café
  check returns "not a café command" so existing flows (creative jobs, QTC, posters) are untouched.
- `CAFE_MODULES=devices,sessions,launcher,assistant,assist` (default list when enabled). Unfinished
  modules (`orders`, `pos`, `members`, `printing`, `reports`) are off until listed.
- Web: `NEXT_PUBLIC_CAFE_ENABLED=1` shows the café navigation. Pages also ask the API
  (`GET /v1/cafe/features`) and show "not enabled" when the server says so.

## 3. Identities and authorization

| Identity | Token | Checked by |
|---|---|---|
| Staff / admin | Existing user JWT (`sub`, `org`, `role`), no `aud` | `deps.get_principal`; café roles: `owner`/`admin` → café admin, `member` → staff |
| Device | Device JWT signed with `CAFE_DEVICE_JWT_SECRET` (falls back to a key derived from `JWT_SECRET` with a distinct label), `aud=mcr-cafe-device`, `sub=device id`, `org`, 15-minute expiry | `device_auth.get_device`; user auth rejects it (unknown `aud`), device auth rejects user tokens (no `aud`, different key) |
| Customer (design) | Seat token issued by the device for the current session, `aud=mcr-cafe-customer`, scoped to seat + session | Allowed only `get_remaining_time`, `launch_app` on own seat, own orders |
| Integration | Scoped API key (existing); café scopes would be `cafe:read` | `deps.required_scope` — v0.1 adds no café scope, so API keys get 403 on `/v1/cafe` |

Device-scoped authorization: every device endpoint resolves the device from the token and only reads or
writes rows for that device's seat. A device can never address another device's commands.

Permission check for `launch_app` (in `service.authorize_launch`), all must pass:

1. Café and `launcher` module enabled for this deployment.
2. Caller is staff/admin of the organisation (or, later, the customer of that seat's active session).
3. Seat belongs to the organisation and has a bound, active device.
4. Device is online (heartbeat within `CAFE_DEVICE_ONLINE_SECONDS`, default 30 s).
5. App is in the organisation's catalogue and enabled.
6. Device's last report says the app is installed.
7. If the app `requires_session`, the seat has an active session with time left.

Failures return a machine-readable code (`DEVICE_OFFLINE`, `APP_NOT_INSTALLED`, `NO_ACTIVE_SESSION`, …)
that Mira turns into a sentence — she never guesses.

## 4. Signed commands

Envelope (canonical JSON: sorted keys, no whitespace, UTF-8):

```json
{"v":1,"command_id":"…","org_id":"…","device_id":"…","seat_label":"12","type":"launch_app",
 "args":{"app_id":"steam"},"issued_at":1764000000,"expires_at":1764000060,"nonce":"…"}
```

- Signed with the server's ECDSA P-256 key (SHA-256, IEEE P1363 `r‖s` signature, base64url).
  `CAFE_COMMAND_SIGNING_KEY` (PEM) in production; in development a key is generated once into
  `DATA_DIR/cafe_command_signing_key.pem`. Production refuses to start café features without it.
- The public key is returned at enrolment and pinned by the client (`server_public_key.pem`); a key
  rotation needs re-pinning by an administrator on the PC.
- Client checks, in order: signature → `device_id` is its own → `org_id` is its own → not expired
  (≤ 60 s life, 30 s clock skew) → `command_id` not seen before (replay cache, persisted) → `type` is one
  it knows → `app_id` exists in the **local** allowlist. Only then does it start the process.
- Command types in v0.1: `launch_app`, `ping`. There is no "run", "shell", "exec", "download" or
  "script" command, and the client has no code path that could perform one.
- Idempotency: `POST /v1/cafe/seats/{id}/launch` and Mira's confirm take an `Idempotency-Key`; the
  same key returns the same command instead of launching twice.

## 5. Windows client (`clients/windows`) — **built** for the slice

.NET 11 (`net11.0-windows`, SDK 11.0.100). The only runtime package is Microsoft's
`System.Security.Cryptography.ProtectedData` (DPAPI); tests use xUnit. See [clients/windows/README.md](../clients/windows/README.md).

| Project | Purpose |
|---|---|
| `McrCafe.Client.Core` | Config, DPAPI credential store, API client, command verifier, allowlist launcher, replay cache, heartbeat and WebSocket loop |
| `McrCafe.Client` | Console host: `register`, `run`, `status`, `help` (staff request) — shows seat and remaining time |
| `McrCafe.Client.Tests` | xUnit tests for signature verification, allowlist, replay, expiry, launcher |
| `McrCafe.DemoGame` (tools) | Stand-in game for demos: logs its PID and exits |

Allowlist (`allowlist.json`, written by the café administrator on the PC, ACL'd to administrators):

```json
{"apps":[{"id":"notepad","name":"Notepad","path":"C:\\Windows\\System32\\notepad.exe","args":[]}]}
```

The server only ever sends an `id`. Path and arguments come from this local file, so even a
compromised server cannot run anything the shop did not allowlist on that PC. Launch uses
`UseShellExecute=false`, an absolute path that must exist, fixed arguments, and no shell.

Install status reported in each heartbeat: for every allowlisted ID, `installed` when the executable
exists, else `missing`.

Print integration (v0.1): `IPrintService` interface and a `HeldPrintQueue` stub that records a
request; nothing is sent to a printer yet.

## 6. Mira: structured tool calling

`POST /v1/cafe/assistant` → `{intent, tool_call, reply, data, confirm}`.

1. **Plan.** If an OpenAI key is configured, Mira calls the chat completions API with `tools`
   (function schemas for `launch_app`, `get_remaining_time`, `request_staff`, `order_food`,
   `top_up_time`, `print_document`) and `tool_choice: "auto"`; the model may only pick a tool and its
   arguments. Without a key, a Thai/English rule parser produces the same structured call. A model
   reply with an unknown tool or bad arguments is discarded and the rules are used.
2. **Ground.** Arguments are resolved against the database (app name → catalogue `app_key`, seat label
   → seat). An app that is not in the catalogue is reported as such — never invented.
3. **Confirm.** Tools with side effects (`launch_app`, `request_staff`, ordering, top-up, print) come
   back with `confirm`. They run only through `POST /v1/cafe/assistant/confirm` with an Idempotency-Key.
4. **Execute.** The executor calls `service.*` — the same code the buttons use, with the same checks.
5. **Reply.** The sentence is built from the result object with fixed Thai/English templates. The
   language model never writes the factual part of the reply, so it cannot invent a success, a price,
   a payment status, availability or device status. For launch, the confirm call waits up to
   `CAFE_LAUNCH_WAIT_SECONDS` (8 s) for the device's real result; if none arrives, Mira says the
   command was sent and is still waiting, and the devices page shows the outcome when it arrives.

Front end: `apps/web/src/lib/cafeAssistant.ts`; `assistant/page.tsx` asks the café endpoint after the
QTC page-name check and before QTC questions, and reuses the QTC confirm card and voice yes/no flow. When the café is disabled the endpoint 404s and Mira carries on.

## 7. Transport

- WebSocket `GET /v1/cafe/device/ws` (WSS behind Caddy in production). The client authenticates by
  sending `{"type":"auth","token":…}` as the first frame within 5 s (tokens are kept out of URLs and
  access logs). Server → client: `command`. Client → server: `heartbeat`, `result`.
- The server delivers commands by reading `cafe_commands` rows (`queued` → `delivered`), so any API
  replica can serve any device; Redis pub/sub can replace the 0.5 s poll later without schema changes.
- HTTP fallbacks with the same checks: `POST /device/heartbeat`, `GET /device/commands`,
  `POST /device/commands/{id}/result`.

## 8. Local store server (design)

Purpose: keep **essential** operations working when the internet is down — open/close sessions, count
time, launch allowlisted apps, sell from the counter for cash, take help requests, queue prints.

- Runs on a small always-on PC or NUC in the shop: the same FastAPI app in `CAFE_ROLE=edge` mode with
  a local PostgreSQL (or SQLite for very small shops), reachable only on the shop LAN, TLS with a
  shop-local CA whose root is pinned by clients at enrolment.
- Identity: the edge server is itself an enrolled device of kind `edge`, with its own key pair. The
  cloud issues it a **delegated signing certificate** (cloud key signs the edge public key, scope
  = this shop, validity ≤ 7 days). Seat clients accept commands signed by the cloud key or by an edge
  key that carries a valid delegation for their organisation. A stolen edge key is limited to one shop
  and expires.
- Data ownership: the edge is authoritative for live sessions, device state and counter sales while
  offline; the cloud is authoritative for catalogue, prices, members and settings (cached read-only).
- Sync: both sides keep an outbox (`cafe_sync_outbox`, monotonic sequence, idempotency key per
  record). On reconnect the edge pushes its events in order; the cloud applies them idempotently and
  pushes back catalogue/price changes. Money rows are append-only ledger entries, so merges never
  overwrite a balance.
- Offline limits: no online payments, no new member sign-ups, no AI (Mira falls back to rules), wallet
  spending capped to the cached balance minus pending offline spend.

## 9. Data model (v0.1 tables)

| Table | Key columns |
|---|---|
| `cafe_seats` | `organization_id`, `label` (unique per org), `zone`, `status` |
| `cafe_enrollment_codes` | `code_hash`, `seat_id`, `expires_at`, `used_at`, `created_by` |
| `cafe_devices` | `seat_id` (unique while active), `name`, `secret_hash`, `status`, `last_heartbeat_at`, `client_version`, `installed_apps` (JSON), `public_ip` |
| `cafe_apps` | `app_key` (unique per org), `name`, `category`, `enabled`, `requires_session` |
| `cafe_sessions` | `seat_id`, `device_id`, `status` (`active`/`ended`), `granted_seconds`, `started_at`, `ended_at`, `customer_ref` |
| `cafe_commands` | `device_id`, `seat_id`, `type`, `args`, `status` (`queued`/`delivered`/`succeeded`/`failed`/`expired`), `envelope`, `signature`, `result`, `idempotency_key`, `requested_by` |
| `cafe_assist_requests` | `seat_id`, `device_id`, `message`, `status` (`open`/`resolved`) |
| `cafe_audit_events` | `entity_type`, `entity_id`, `action`, `actor`, `detail` |

Planned for later modules: `cafe_menu_items`, `cafe_orders`, `cafe_order_lines`, `cafe_print_jobs`,
`cafe_members`, `cafe_wallet_ledger`, `cafe_time_packages`, `cafe_payments`, `cafe_shifts`.

## 10. Security limitations in v0.1

- A device secret on a PC where the customer has gained administrator rights can be read; it then
  acts as that one seat only. Mitigation (design): run the client as a Windows service under a
  dedicated account, TPM-bound keys.
- Results reported by the client are authenticated by the device token over TLS but not separately
  signed by a device key.
- The signing key in development is a file on disk; production must supply `CAFE_COMMAND_SIGNING_KEY`
  from a secret store. There is no automatic key rotation.
- The WebSocket delivery loop polls the database every 0.5 s per connected device; fine for one shop,
  should move to Redis pub/sub for many shops.
- No customer identity yet: in v0.1 only staff/admin (through Mira or the devices page) can launch.

## 11. Verification (v0.1)

| What | How | Result on 2026-11-24 |
|---|---|---|
| API slice | `apps/api/tests/test_cafe.py` (13 tests: flags, enrol, bind, heartbeat, launch checks, identities, tenant isolation, WebSocket, Mira, tool calling) | pass; full backend suite 231 passed, 1 skipped |
| Migration | `alembic upgrade head` on a fresh SQLite database; downgrade to 0011 and up again; `alembic check` | pass (PostgreSQL not run) |
| Windows client | `dotnet test clients/windows/McrCafe.slnx` (33 tests incl. a Python-signed vector) | pass |
| Web | `vitest` (154 tests incl. 7 café), `npm run typecheck`, `eslint`, `next build` | pass |
| End to end | `scripts/cafe_e2e_demo.py`: real API process + real `mcr-cafe-client` process; Mira (Thai) → confirm → signed command → client launches `mcr-demo-game.exe` → PID matches the game's own log | pass |
| `/cafe/devices` in a browser | not checked live (needs a sign-in); page covered by component tests and `next build` | — |
