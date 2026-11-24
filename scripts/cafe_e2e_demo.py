"""MCR Smart Café end-to-end check: a real Windows client process, a running API, and Mira.

Steps (each one checks the real response; the script stops at the first failure):
  1. sign in as a café owner; create seat 12, an enrolment code and the approved app "demo-game"
  2. register the C# client (`mcr-cafe-client register`) with that code; write its local allowlist
  3. start the client (`mcr-cafe-client run`) as a separate process; wait until /cafe/seats shows the PC online with
     demo-game installed
  4. start a 60-minute session
  5. ask Mira in Thai to open the game; confirm; the server signs a command, the client verifies and launches
     mcr-demo-game.exe, and Mira's reply is built from the client's real result
  6. check the demo game really ran (its started.log has the PID the client reported)
  7. ask Mira for the remaining time

Usage (from the repo root, API running with CAFE_ENABLED=true, client built with `dotnet build clients/windows/McrCafe.slnx`):
  uv run --directory apps/api --env-file <env> python ../../scripts/cafe_e2e_demo.py --api http://127.0.0.1:8011 [--keep-running]
Credentials come from BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD in the environment and are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "clients/windows/src/McrCafe.Client/bin/Debug/net11.0-windows/mcr-cafe-client.exe"
GAME = ROOT / "clients/windows/tools/McrCafe.DemoGame/bin/Debug/net11.0/mcr-demo-game.exe"


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}", flush=True)


def check(cond: bool, message: str) -> None:
    if not cond:
        print(f"FAILED: {message}", file=sys.stderr)
        sys.exit(1)
    print(f"    ok: {message}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Thai replies on a Windows console
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8011")
    ap.add_argument("--seat", default="12")
    ap.add_argument("--data-dir", default=str(Path(tempfile.gettempdir()) / "mcr-cafe-e2e-client"))
    ap.add_argument("--keep-running", action="store_true", help="leave the client running afterwards (for the web UI)")
    args = ap.parse_args()
    for exe in (CLIENT, GAME):
        check(exe.exists(), f"built {exe.name}")

    api = httpx.Client(base_url=args.api, timeout=30)
    step(1, "owner signs in; seat, enrolment code and approved app")
    tok = api.post("/v1/auth/login", json={"email": os.environ["BOOTSTRAP_ADMIN_EMAIL"], "password": os.environ["BOOTSTRAP_ADMIN_PASSWORD"]})
    check(tok.status_code == 200, "signed in")
    h = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    seats = api.get("/v1/cafe/seats", headers=h).json()["items"]
    seat = next((s for s in seats if s["label"] == args.seat), None)
    if seat is None:
        seat = api.post("/v1/cafe/seats", json={"label": args.seat, "zone": "A"}, headers=h).json()
    check("id" in seat, f"seat {args.seat} exists")
    code = api.post(f"/v1/cafe/seats/{seat['id']}/enrollment-code", headers=h).json()["code"]
    check(len(code) == 14, "one-time enrolment code issued")
    r = api.post("/v1/cafe/apps", json={"app_key": "demo-game", "name": "Demo Game", "aliases": ["เกมทดสอบ"], "category": "game"}, headers=h)
    check(r.status_code in (201, 409), "demo-game is in the café catalogue")

    step(2, "register the Windows client process with the code")
    data = Path(args.data_dir)
    if data.exists():
        for f in data.glob("*"):
            if f.is_file():
                f.unlink()
    reg = subprocess.run([str(CLIENT), "--data-dir", str(data), "register", "--server", args.api, "--code", code, "--name", f"PC-{args.seat}"],
                         capture_output=True, text=True, timeout=60)
    print("    " + reg.stdout.strip().replace("\n", "\n    "))
    check(reg.returncode == 0, "client registered and pinned the server key")
    (data / "allowlist.json").write_text(json.dumps({"apps": [{"id": "demo-game", "name": "Demo Game", "path": str(GAME)}]}, indent=2))
    check("BEGIN PUBLIC KEY" in (data / "server_public_key.pem").read_text(), "server public key stored")
    check(b"mcd_" not in (data / "device.secret").read_bytes(), "device secret is DPAPI-encrypted on disk")

    step(3, "start the client process (WebSocket + heartbeat)")
    log = open(data / "client.log", "w", encoding="utf-8")  # noqa: SIM115 - outlives this function with --keep-running
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen([str(CLIENT), "--data-dir", str(data), "run", "--once-seconds", "900" if args.keep_running else "60"],
                            stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    deadline = time.time() + 30
    row = None
    while time.time() < deadline:
        row = next(s for s in api.get("/v1/cafe/seats", headers=h).json()["items"] if s["id"] == seat["id"])
        if row["device"] and row["device"]["online"] and row["device"]["installed_apps"].get("demo-game") == "installed":
            break
        time.sleep(0.5)
    online = bool(row and row["device"] and row["device"]["online"])
    check(online, f"seat {args.seat} PC is online (client {row['device']['client_version'] if online else '?'})")
    check(row["device"]["installed_apps"] == {"demo-game": "installed"}, "client reports demo-game installed")

    step(4, "staff start a 60-minute session")
    if not row["session"] or row["session"]["remaining_seconds"] <= 0:
        check(api.post(f"/v1/cafe/seats/{seat['id']}/sessions", json={"minutes": 60}, headers=h).status_code == 201, "session started")

    step(5, "Mira: เปิดเกม Demo Game ที่เครื่อง 12 ให้หน่อย")
    started_log = GAME.parent / "started.log"
    before = started_log.read_text().count("\n") if started_log.exists() else 0
    ask = api.post("/v1/cafe/assistant", json={"text": f"เปิดเกม Demo Game ที่เครื่อง {args.seat} ให้หน่อย"}, headers=h).json()
    print(f"    tool call: {ask['tool_call']}")
    print(f"    Mira: {ask['reply']}")
    check(ask["intent"] == "launch_app" and ask["confirm"] is not None, "Mira proposes the launch and waits for a yes")
    done = api.post("/v1/cafe/assistant/confirm", json={**ask["confirm"], "idempotency_key": f"e2e-{time.time()}"}, headers=h).json()
    print(f"    Mira: {done['reply']}")
    print(f"    command: {done['data'].get('status')} {done['data'].get('result')}")
    check(done["data"].get("status") == "succeeded", "seat PC verified the signed command and reported STARTED")

    step(6, "the demo game really ran")
    time.sleep(1)
    lines = started_log.read_text().splitlines() if started_log.exists() else []
    check(len(lines) > before, "mcr-demo-game.exe wrote a new line to started.log")
    check(f"pid={done['data']['result']['pid']}" in lines[-1], f"its PID matches the client's report ({done['data']['result']['pid']})")

    step(7, "Mira: เหลือเวลาเท่าไหร่")
    left = api.post("/v1/cafe/assistant", json={"text": f"เครื่อง {args.seat} เหลือเวลาเท่าไหร่"}, headers=h).json()
    print(f"    Mira: {left['reply']}")
    check(left["data"].get("session_active") is True, "remaining time comes from the session")

    cmds = api.get("/v1/cafe/commands", headers=h).json()["items"]
    print(f"\nlast command on /cafe/devices: {cmds[0]['status']} via {cmds[0]['via']} -> {cmds[0]['result']}")
    if args.keep_running:
        print(f"client left running (pid {proc.pid}, stops by itself after 15 minutes); log: {data / 'client.log'}")
    else:
        proc.wait(timeout=90)
        log.close()
        print((data / "client.log").read_text())
    print("END-TO-END OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
