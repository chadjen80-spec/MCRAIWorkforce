# MCR Smart Café — Product Requirements

Status: draft v0.1 (2026-11-24). Owner: MCR Technology. Scope: an AI-assisted management system for
internet cafés and gaming centres (Thai "ร้านเกม / PC café"), built on the MCR AI Workforce platform.

The design takes ideas from two kinds of product: the seat clients used in Korean PC cafés (a dark
in-seat overlay with remaining time, food ordering, notices and a game launcher) and conversational
"digital human" kiosks that answer by voice and show a structured table. **No vendor's name, logo,
artwork, layout assets or wording is reused.** All visuals are MCR's own.

## 1. Goals

1. Let a customer at a seat do everything without walking to the counter: see remaining time, top up,
   order food, launch a game, print, and call staff — by touch or by talking to Mira in Thai.
2. Give the owner one place to run the shop: seats and devices, sessions, the game library, orders,
   print jobs, members and packages, and reports.
3. Keep the shop running when the internet is down (sessions, time, launching, cash sales).
4. Never let the AI do anything the same person could not do by pressing a button, and never let it
   run arbitrary programs or commands on a seat PC.

### Non-goals

- Bypassing game licensing, publisher launchers, anti-cheat, Windows security or commercial software
  restrictions. The client launches what the café installed and licensed; it does not crack, inject,
  hook or hide anything.
- Being an anti-cheat, a DRM or a parental-control product.
- Payments through a provider before that provider's contract, sandbox and reconciliation are in place.

## 2. Users and identities

Four identities that never share credentials or tokens:

| Identity | Who | Authenticates with | Can |
|---|---|---|---|
| Customer | A walk-in guest or member at a seat | Seat session (guest) or member login (phone/PIN, later) | Act on **their own** seat and session only |
| Staff | Counter staff | MCR user account, role `member` | Start/stop sessions, release prints, answer help requests, launch apps on seats |
| Administrator | Owner / manager | MCR user account, role `owner`/`admin` | Everything staff can, plus enrol devices, edit the app catalogue, prices and settings |
| Device | A seat PC running the MCR Smart Café client | Device ID + device secret (issued once at enrolment) → short-lived device token | Report heartbeat and install status, receive signed commands, report results, raise help and print requests for its own seat |

Integrations (n8n, accounting) keep using scoped API keys and never act as a device or customer.

## 3. Modules

Each module ships behind its own feature flag (`CAFE_MODULES`), and the whole café product behind
`CAFE_ENABLED`. A disabled module's API answers `404 FEATURE_DISABLED` and its page says so.

| Module | Flag | What it does | v0.1 status |
|---|---|---|---|
| PC session management | `devices`, `sessions` | Seats, device enrolment, heartbeat, online/offline, start/end session, remaining time | **Slice built** (enrol, bind, heartbeat, sessions with granted minutes) |
| Game launcher | `launcher` | Café app catalogue, per-device install status, launch by allowlist ID | **Slice built** |
| AI customer kiosk / Mira | `assistant` | Thai voice/text commands with structured tool calls, answers from real results | **Slice built** for launch, remaining time, staff help; other tools answer "not enabled" |
| Staff assistance | `assist` | Seat → staff help request, shown on the devices page | **Built** (request + list + resolve) |
| Voice commerce (F&B) | `orders` | Menu, cart, order to the kitchen, order history | Designed only |
| POS and payment | `pos` | Counter sales, cash drawer, PromptPay QR, receipts, shift close | Designed only |
| Membership and time packages | `members` | Member accounts, wallet, time packages, points | Designed only |
| Smart print | `printing` | Seat uploads a document, staff releases it, per-page price | Designed only (client interface stub) |
| Business analytics | `reports` | Occupancy, revenue by module, top games, peak hours | Designed only |

## 4. Customer experience (seat)

A dark gaming interface with luminous blue accents, small enough not to cover the game:

- Top bar: seat number, remaining time (counts down locally, re-synced from the server), membership
  status, wallet balance.
- Compact Mira panel (avatar + one line of text + microphone). It never opens full-screen over a game.
- Tabs: Games (library with install status), Food & drinks, Print, Time packages, Orders, Help.
- "Call staff" is always one tap away.

### Mira at the seat (Thai first)

Examples Mira must understand:

| Say | Tool | Answer comes from |
|---|---|---|
| "เปิด Steam ให้หน่อย", "อยากเล่น Valorant" | `launch_app` | The client's real launch result (started / not installed / refused) |
| "เหลือเวลาเท่าไหร่" | `get_remaining_time` | The active session row |
| "เติมเวลา 2 ชั่วโมง" | `top_up_time` | Packages + payment result (module off → "not enabled yet") |
| "สั่งมาม่าต้มยำ 1 ที่ กับโค้ก" | `order_food` | Menu prices + order result (module off → "not enabled yet") |
| "ปริ้นไฟล์นี้ 2 แผ่น" | `print_document` | Print job record (module off → "not enabled yet") |
| "เรียกพนักงานหน่อย" | `request_staff` | The created help request |

Rules: Mira only says what the backend returned. She never states a price, a payment status, that an
order went through, that a game is available or that a PC is online unless that came from a tool result
in the same turn. Actions that spend money always need an explicit yes.

## 5. Merchant experience

Pages: `/cafe` (overview), `/cafe/devices`, `/cafe/sessions`, `/cafe/games`, `/cafe/orders`,
`/cafe/printing`, `/cafe/members`, `/cafe/reports`, `/cafe/settings`. Each module page that is not
enabled shows what it will do and how to enable it, rather than fake data.

`/cafe/devices` (v0.1): seats with their bound device, online/offline from the last heartbeat, client
version, installed approved apps, active session and remaining time, open help requests, recent
commands with their real result, and enrolment codes.

## 6. Requirements

### Functional (v0.1 slice)

- F1 An administrator creates a one-time enrolment code for a seat (expires in 15 minutes, shown once).
- F2 The Windows client registers with the code and receives a device ID and secret; the secret is
  stored with Windows DPAPI and the server keeps only a hash.
- F3 The client exchanges its secret for a device token, sends heartbeats (every 10 s) with its version
  and install status, and keeps a WebSocket open for commands.
- F4 Staff start a session on a seat with granted minutes; the seat and Mira can read remaining time.
- F5 Mira turns "เปิด <app>" into a `launch_app` tool call; after confirmation the server checks
  permission, signs a command and sends it to the seat's device; the client verifies and launches
  the allowlisted app and reports the real result, which Mira speaks and `/cafe/devices` shows.

### Non-functional

- Tenant isolation on every query (`organization_id`), role checks, device-scoped authorization.
- Signed commands (ECDSA P-256), short expiry, replay protection, idempotency keys on writes.
- Audit row for every enrolment, session change, command and result, written in the same transaction.
- Money as `NUMERIC`, financial operations in one database transaction (when payment modules land).
- Essential operations keep working offline through the local store server (see architecture).
- Thai and English UI text.

## 7. Success metrics (after pilot)

- 90 % of launch requests by voice reach the seat and report a result in under 3 s on shop LAN.
- Zero commands executed that were not on the seat's local allowlist (audited).
- Counter visits for "how much time do I have" and "please open X" drop by half.

## 8. Risks

- Customers try to make Mira run other programs → only allowlist IDs exist as a tool argument; the
  client refuses anything else even if the server is compromised.
- Seat PC tampering (customer gains admin) → client runs as a service under a restricted account,
  secret in DPAPI machine scope; a stolen secret only acts as that one seat. Documented limitation.
- Internet outage during a paid session → local store server keeps time and launching working.
- Payment disputes → no payment modules until provider sandbox, reconciliation and receipts are verified.
