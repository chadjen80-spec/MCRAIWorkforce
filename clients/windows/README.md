# MCR Smart Café — Windows seat client

A small .NET 11 client for café seat PCs. It registers the PC to one seat, keeps a WebSocket to the MCR API, sends a
heartbeat with the install status of approved programs, and runs **signed** commands — today only `launch_app` (by
allowlist ID) and `ping`. It cannot run shell commands, scripts, downloads or anything not on its local allowlist.

Design and threat model: [docs/MCR_SMART_CAFE_ARCHITECTURE.md](../../docs/MCR_SMART_CAFE_ARCHITECTURE.md).

## Requirements

- Windows 10/11, .NET SDK 11.0.100 or later (runtime 11.0). Built and tested with SDK 11.0.100.
- NuGet packages (restored by `dotnet build`): `System.Security.Cryptography.ProtectedData` 9.0.0 (Microsoft) for the
  client; xUnit 2.9.3 and Microsoft.NET.Test.Sdk for the tests.

## Build and test

```powershell
dotnet build clients/windows/McrCafe.slnx
dotnet test clients/windows/McrCafe.slnx
```

## Use

1. In the web app, `/cafe/devices`: add the seat and press **Enrolment code** (one-time, 15 minutes).
2. On the seat PC, as an administrator:

   ```powershell
   mcr-cafe-client register --server https://cafe.example.com --code 3F2A-91BC-07DE
   ```

   This stores `device.json`, the DPAPI-encrypted `device.secret` and the pinned `server_public_key.pem` in
   `%ProgramData%\MCR\SmartCafe` (override with `--data-dir`). Restrict that folder to Administrators and SYSTEM.
3. List approved programs in `allowlist.json` in the same folder. IDs must match the café catalogue (`/cafe/games`):

   ```json
   {"apps": [{"id": "steam", "name": "Steam", "path": "C:\\Program Files (x86)\\Steam\\steam.exe", "args": []}]}
   ```

   Only absolute `.exe` paths are accepted; shells and script hosts (`cmd`, `powershell`, `wscript`, `mshta`, …),
   scripts and network paths are refused.
4. `mcr-cafe-client run` — connects, shows the seat and remaining time in the console title, executes commands.

Other commands: `status`, `help "message"` (call staff), `apps` (allowlist and install status),
`print <file>` (held locally; the printing module is not built yet, nothing is printed or charged).

Plain `http://` is accepted only for `localhost`/`127.0.0.1` (development).

## What a command must pass before anything starts

Signature (ECDSA P-256 over the exact envelope, pinned key) → envelope version → this device and organisation →
not expired (≤ 60 s life, 30 s skew; long-lived envelopes refused) → not replayed (persisted cache) → known type →
`args` contains only `app_id` with a simple ID → ID on the local allowlist → program file exists. The result
(`STARTED` + PID, or the refusal code) goes back to the server, which shows it on `/cafe/devices` and in Mira's reply.

## Projects

| Project | Purpose |
|---|---|
| `src/McrCafe.Client.Core` | Allowlist, command verifier, replay cache, launcher, DPAPI store, API client, WebSocket agent, print stub |
| `src/McrCafe.Client` | Console host (`mcr-cafe-client.exe`) |
| `tools/McrCafe.DemoGame` | Stand-in "game" for demos: logs its PID to `started.log` and exits |
| `tests/McrCafe.Client.Tests` | xUnit tests, including a command signed by the Python server (`Fixtures/python_vector.json`) |

End-to-end check against a running API: `scripts/cafe_e2e_demo.py`.

## Not done yet

- No Windows service wrapper, overlay window or kiosk lockdown; run it as a console app for now.
- No local store server (offline mode) and no customer login on the seat.
- Results are authenticated by the device token over TLS, not separately signed by a device key.
