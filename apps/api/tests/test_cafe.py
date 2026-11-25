"""MCR Smart Café vertical slice: enrol a seat PC, bind it to a seat, heartbeat, launch an approved app through the API,
the WebSocket and Mira, with the device verifying signed commands and reporting real results."""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.cafe import signing
from app.cafe.models import CafeCommand, CafeEnrollmentCode
from app.db import session_scope, utcnow
from app.main import app
from app.models import Membership, User
from app.security import hash_password
from app.services import router as hybrid_router
from tests.conftest import PASSWORD


@pytest.fixture
def cafe(fresh_state):
    fresh_state.cafe_enabled = True
    fresh_state.cafe_launch_wait_seconds = 3
    return fresh_state


def _seat(client, auth, label="12"):
    r = client.post("/v1/cafe/seats", json={"label": label, "zone": "A"}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _enrol(client, auth, seat):
    code = client.post(f"/v1/cafe/seats/{seat['id']}/enrollment-code", headers=auth).json()["code"]
    r = client.post("/v1/cafe/device/register", json={"enrollment_code": code, "name": "PC-12", "client_version": "0.1.0"})
    assert r.status_code == 201, r.text
    reg = r.json()
    tok = client.post("/v1/cafe/device/token", json={"device_id": reg["device_id"], "device_secret": reg["device_secret"]})
    assert tok.status_code == 200, tok.text
    reg["headers"] = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    return reg


def _ready(client, auth, *, installed=("steam",), session_minutes=60):
    """A seat with an enrolled, online PC, Steam in the catalogue and (optionally) an active session."""
    seat = _seat(client, auth)
    dev = _enrol(client, auth, seat)
    client.post("/v1/cafe/apps", json={"app_key": "steam", "name": "Steam", "aliases": ["สตีม"], "category": "launcher"}, headers=auth)
    hb = client.post("/v1/cafe/device/heartbeat", headers=dev["headers"],
                     json={"client_version": "0.1.0", "installed_apps": {k: "installed" for k in installed}})
    assert hb.status_code == 200, hb.text
    if session_minutes:
        assert client.post(f"/v1/cafe/seats/{seat['id']}/sessions", json={"minutes": session_minutes}, headers=auth).status_code == 201
    return seat, dev


def _answer_commands(dev, *, code="STARTED", ok=True, stop: threading.Event, seen: list):
    """Plays the seat PC: fetches commands, checks the signature like the C# client, reports a result."""
    c = TestClient(app)
    while not stop.is_set():
        for w in c.get("/v1/cafe/device/commands", headers=dev["headers"]).json().get("items", []):
            assert signing.verify(w["envelope"], w["signature"], dev["server_public_key"])
            env = json.loads(w["envelope"])
            seen.append(env)
            c.post(f"/v1/cafe/device/commands/{w['command_id']}/result", headers=dev["headers"],
                   json={"ok": ok, "code": code, "detail": {"pid": 4242, "app_id": env["args"]["app_id"]}})
        time.sleep(0.05)


# ---- feature flags --------------------------------------------------------------------------------------------
def test_cafe_is_off_by_default_and_does_not_touch_mira(client, auth):
    r = client.get("/v1/cafe/seats", headers=auth)
    assert r.status_code == 404 and r.json()["error"]["code"] == "FEATURE_DISABLED"
    assert client.post("/v1/cafe/device/register", json={"enrollment_code": "AAAA-BBBB-CCCC"}).status_code == 404
    out = client.post("/v1/cafe/assistant", json={"text": "เปิด Steam ที่เครื่อง 12"}, headers=auth).json()
    assert out["intent"] is None
    assert client.get("/v1/cafe/features", headers=auth).json()["enabled"] is False
    # the existing Quote-to-Cash assistant still answers
    assert client.post("/v1/qtc/assistant", json={"text": "ลูกค้าคนไหนยังไม่จ่ายเงิน"}, headers=auth).json()["intent"] == "unpaid_customers"


def test_unfinished_modules_stay_off(cafe, client, auth):
    feats = client.get("/v1/cafe/features", headers=auth).json()["modules"]
    assert feats["launcher"] and feats["devices"] and feats["orders"] and not feats["printing"] and not feats["pos"]
    _seat(client, auth)
    out = client.post("/v1/cafe/assistant", json={"text": "ปริ้นไฟล์นี้ 2 แผ่น"}, headers=auth).json()
    assert out["intent"] == "print_document" and out["confirm"] is None and out["data"]["enabled"] is False
    assert "ยังไม่เปิดใช้งาน" in out["reply"]


# ---- the vertical slice over HTTP -------------------------------------------------------------------------------------
def test_enrol_bind_heartbeat_launch_and_real_result(cafe, client, auth):
    seat, dev = _ready(client, auth)
    assert dev["seat_label"] == "12" and "BEGIN PUBLIC KEY" in dev["server_public_key"]
    row = client.get("/v1/cafe/seats", headers=auth).json()["items"][0]
    assert row["device"]["online"] and row["device"]["installed_apps"] == {"steam": "installed"}
    assert 3500 < row["session"]["remaining_seconds"] <= 3600

    r = client.post(f"/v1/cafe/seats/{seat['id']}/launch", json={"app_key": "steam"}, headers={**auth, "Idempotency-Key": "k-1"})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    again = client.post(f"/v1/cafe/seats/{seat['id']}/launch", json={"app_key": "steam"}, headers={**auth, "Idempotency-Key": "k-1"})
    assert again.json()["id"] == r.json()["id"]  # same key, same command

    items = client.get("/v1/cafe/device/commands", headers=dev["headers"]).json()["items"]
    assert len(items) == 1
    env = json.loads(items[0]["envelope"])
    assert signing.verify(items[0]["envelope"], items[0]["signature"], dev["server_public_key"])
    assert not signing.verify(items[0]["envelope"].replace("steam", "cmdexe"), items[0]["signature"], dev["server_public_key"])
    assert env["type"] == "launch_app" and env["args"] == {"app_id": "steam"} and env["device_id"] == dev["device_id"]
    assert env["expires_at"] - env["issued_at"] == 60
    assert client.get("/v1/cafe/device/commands", headers=dev["headers"]).json()["items"] == []  # delivered once

    res = client.post(f"/v1/cafe/device/commands/{env['command_id']}/result", headers=dev["headers"],
                      json={"ok": True, "code": "STARTED", "detail": {"pid": 77, "path": "C:\\evil.exe"}})
    assert res.json()["status"] == "succeeded"
    cmd = client.get(f"/v1/cafe/commands/{env['command_id']}", headers=auth).json()
    assert cmd["status"] == "succeeded" and cmd["result"] == {"code": "STARTED", "message": "", "pid": 77}  # unknown detail dropped
    actions = [e["action"] for e in client.get("/v1/cafe/audit", headers=auth).json()["items"]]
    for a in ("device.registered", "session.started", "command.queued", "command.succeeded"):
        assert a in actions


def test_launch_permission_checks(cafe, client, auth):
    seat, dev = _ready(client, auth, installed=(), session_minutes=0)

    def launch(key="steam"):
        return client.post(f"/v1/cafe/seats/{seat['id']}/launch", json={"app_key": key}, headers=auth).json()["error"]["code"]

    assert launch("cmd") == "APP_NOT_IN_CATALOG"
    assert launch() == "APP_NOT_INSTALLED"
    client.post("/v1/cafe/device/heartbeat", headers=dev["headers"], json={"installed_apps": {"steam": "installed"}})
    assert launch() == "NO_ACTIVE_SESSION"
    client.post(f"/v1/cafe/seats/{seat['id']}/sessions", json={"minutes": 30}, headers=auth)
    with session_scope() as s:  # the PC stopped sending heartbeats a minute ago
        from app.cafe.models import CafeDevice

        s.get(CafeDevice, uuid.UUID(dev["device_id"])).last_heartbeat_at = utcnow() - timedelta(seconds=61)
    assert launch() == "DEVICE_OFFLINE"
    with session_scope() as s:
        assert s.query(CafeCommand).count() == 0  # nothing was signed or queued


def test_identities_are_separate(cafe, client, auth, org_a, org_b):
    seat, dev = _ready(client, auth)
    # a device token is not a staff token, and a staff token is not a device token
    assert client.get("/v1/cafe/seats", headers=dev["headers"]).status_code == 401
    assert client.get("/v1/cafe/device/commands", headers=auth).status_code == 401
    # integration keys cannot run café operations
    assert client.get("/v1/cafe/seats", headers={"X-API-Key": org_a["api_key"]}).status_code == 403
    # another tenant's staff cannot see or launch on this seat
    tok_b = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()["access_token"]
    auth_b = {"Authorization": f"Bearer {tok_b}"}
    assert client.get("/v1/cafe/seats", headers=auth_b).json()["items"] == []
    assert client.post(f"/v1/cafe/seats/{seat['id']}/launch", json={"app_key": "steam"}, headers=auth_b).status_code == 404
    # staff (member role) can launch but cannot enrol PCs
    with session_scope() as s:
        u = User(email="staff-a@example.com", full_name="Staff", password_hash=hash_password(PASSWORD))
        s.add(u)
        s.flush()
        s.add(Membership(organization_id=org_a["org_id"], user_id=u.id, role="member"))
    tok = client.post("/v1/auth/login", json={"email": "staff-a@example.com", "password": PASSWORD}).json()["access_token"]
    staff = {"Authorization": f"Bearer {tok}"}
    assert client.post(f"/v1/cafe/seats/{seat['id']}/enrollment-code", headers=staff).status_code == 403
    assert client.post(f"/v1/cafe/seats/{seat['id']}/launch", json={"app_key": "steam"}, headers=staff).status_code == 202
    # a second PC cannot answer the first PC's command
    seat2 = _seat(client, auth, "13")
    dev2 = _enrol(client, auth, seat2)
    cmd_id = client.get("/v1/cafe/device/commands", headers=dev["headers"]).json()["items"][0]["command_id"]
    r = client.post(f"/v1/cafe/device/commands/{cmd_id}/result", headers=dev2["headers"], json={"ok": True, "code": "STARTED"})
    assert r.status_code == 404
    # a revoked PC loses access immediately
    client.post(f"/v1/cafe/devices/{dev['device_id']}/revoke", headers=auth)
    assert client.get("/v1/cafe/device/status", headers=dev["headers"]).status_code == 401


def test_enrolment_code_is_single_use_and_expires(cafe, client, auth):
    seat = _seat(client, auth)
    code = client.post(f"/v1/cafe/seats/{seat['id']}/enrollment-code", headers=auth).json()["code"]
    assert client.post("/v1/cafe/device/register", json={"enrollment_code": code}).status_code == 201
    assert client.post("/v1/cafe/device/register", json={"enrollment_code": code}).status_code == 401
    code2 = client.post(f"/v1/cafe/seats/{seat['id']}/enrollment-code", headers=auth).json()["code"]
    with session_scope() as s:
        for row in s.query(CafeEnrollmentCode).filter(CafeEnrollmentCode.used_at.is_(None)):
            row.expires_at = utcnow() - timedelta(seconds=1)
    assert client.post("/v1/cafe/device/register", json={"enrollment_code": code2}).status_code == 401


# ---- WebSocket channel -------------------------------------------------------------------------------------------------
def test_websocket_delivers_signed_command_and_takes_result(cafe, client, auth):
    seat, dev = _ready(client, auth)
    token = dev["headers"]["Authorization"][7:]
    with client.websocket_connect("/v1/cafe/device/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        assert ws.receive_json()["type"] == "welcome"
        ws.send_text(json.dumps({"type": "heartbeat", "client_version": "0.1.0", "installed_apps": {"steam": "installed"}}))
        status = ws.receive_json()
        assert status["type"] == "status" and status["session_active"] and status["seat_label"] == "12"
        cmd = client.post(f"/v1/cafe/seats/{seat['id']}/launch", json={"app_key": "steam"}, headers=auth).json()
        msg = ws.receive_json()
        assert msg["type"] == "command" and msg["command_id"] == cmd["id"]
        assert signing.verify(msg["envelope"], msg["signature"], dev["server_public_key"])
        ws.send_text(json.dumps({"type": "result", "command_id": cmd["id"], "ok": False, "code": "NOT_ALLOWLISTED", "message": "steam"}))
        ack = ws.receive_json()
        assert ack == {"type": "ack", "command_id": cmd["id"], "status": "failed"}
    assert client.get(f"/v1/cafe/commands/{cmd['id']}", headers=auth).json()["result"]["code"] == "NOT_ALLOWLISTED"


def test_websocket_rejects_staff_token(cafe, client, auth):
    _ready(client, auth)
    with client.websocket_connect("/v1/cafe/device/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": auth["Authorization"][7:]}))
        with pytest.raises(Exception):  # noqa: B017 - closed with 4401
            ws.receive_json()


# ---- Mira ---------------------------------------------------------------------------------------------------------------
def test_mira_launch_speaks_the_real_result(cafe, client, auth):
    seat, dev = _ready(client, auth)
    out = client.post("/v1/cafe/assistant", json={"text": "เปิด สตีม ที่เครื่อง 12 ให้หน่อย"}, headers=auth).json()
    assert out["intent"] == "launch_app" and out["tool_call"]["source"] == "rules"
    assert out["confirm"] == {"action": "launch_app", "payload": {"seat_id": seat["id"], "app_key": "steam"}}
    with session_scope() as s:
        assert s.query(CafeCommand).count() == 0  # nothing happens before a yes

    stop, seen = threading.Event(), []
    worker = threading.Thread(target=_answer_commands, args=(dev,), kwargs={"stop": stop, "seen": seen}, daemon=True)
    worker.start()
    try:
        done = client.post("/v1/cafe/assistant/confirm", json={**out["confirm"], "idempotency_key": "mira-1"}, headers=auth).json()
    finally:
        stop.set()
        worker.join(5)
    assert done["data"]["status"] == "succeeded" and done["reply"] == "เปิด Steam ที่ที่นั่ง 12 แล้วค่ะ"
    assert seen[0]["args"] == {"app_id": "steam"}


def test_mira_explains_a_program_that_needs_admin_rights(cafe, client, auth):
    seat, dev = _ready(client, auth)
    out = client.post("/v1/cafe/assistant", json={"text": "เปิด Steam ที่เครื่อง 12"}, headers=auth).json()
    stop, seen = threading.Event(), []
    worker = threading.Thread(target=_answer_commands, args=(dev,), kwargs={"stop": stop, "seen": seen, "ok": False, "code": "NEEDS_ELEVATION"},
                              daemon=True)
    worker.start()
    try:
        done = client.post("/v1/cafe/assistant/confirm", json=out["confirm"], headers=auth).json()
    finally:
        stop.set()
        worker.join(5)
    assert done["data"]["status"] == "failed" and done["data"]["result"]["code"] == "NEEDS_ELEVATION"
    assert done["reply"].startswith("Steam ต้องใช้สิทธิ์ผู้ดูแลระบบ") and "แล้วค่ะ" not in done["reply"]


def test_mira_reports_refusals_and_waiting_honestly(cafe, client, auth):
    cafe.cafe_launch_wait_seconds = 0.3
    seat, dev = _ready(client, auth)
    out = client.post("/v1/cafe/assistant", json={"text": "เปิด Steam ที่เครื่อง 12"}, headers=auth).json()
    done = client.post("/v1/cafe/assistant/confirm", json=out["confirm"], headers=auth).json()
    assert done["data"]["status"] == "queued" and "กำลังรอ" in done["reply"] and "แล้วค่ะ" not in done["reply"].split("กำลังรอ")[1]
    # client refuses: the reply says so
    cid = client.get("/v1/cafe/device/commands", headers=dev["headers"]).json()["items"][0]["command_id"]
    client.post(f"/v1/cafe/device/commands/{cid}/result", headers=dev["headers"], json={"ok": False, "code": "NOT_ALLOWLISTED"})
    # unknown game: no proposal, no command
    unknown = client.post("/v1/cafe/assistant", json={"text": "อยากเล่นเกม Minecraft เครื่อง 12"}, headers=auth).json()
    assert unknown["intent"] == "launch_app" and unknown["confirm"] is None and unknown["data"]["error"] == "APP_NOT_IN_CATALOG"
    # remaining time comes from the session row
    left = client.post("/v1/cafe/assistant", json={"text": "เครื่อง 12 เหลือเวลาเท่าไหร่"}, headers=auth).json()
    assert left["intent"] == "get_remaining_time" and left["data"]["session_active"] and "เหลือเวลา 59 นาที" in left["reply"]
    # staff help goes through confirm and creates a request
    help_ = client.post("/v1/cafe/assistant", json={"text": "เรียกพนักงานที่เครื่อง 12"}, headers=auth).json()
    assert help_["confirm"]["action"] == "request_staff"
    client.post("/v1/cafe/assistant/confirm", json=help_["confirm"], headers=auth)
    assert client.get("/v1/cafe/assist", headers=auth).json()["items"][0]["source"] == "mira"
    assert unknown["data"]["app"] == "Minecraft"
    # speech heard "สตรีม" (stream) for "สตีม" (Steam): one-letter slips still find the app, and Mira asks first
    misheard = client.post("/v1/cafe/assistant", json={"text": "เปิดสตรีมที่เครื่อง 12"}, headers=auth).json()
    assert misheard["intent"] == "launch_app" and misheard["confirm"]["payload"]["app_key"] == "steam"
    # "open X at seat 12" with an unknown X is still a launch request (not a creative job), answered honestly
    odd = client.post("/v1/cafe/assistant", json={"text": "เปิดโปรแกรมแต่งรูปที่เครื่อง 12"}, headers=auth).json()
    assert odd["intent"] == "launch_app" and odd["confirm"] is None and odd["data"]["error"] == "APP_NOT_IN_CATALOG"
    # creative and sales requests, and requests for a café page, are not café commands
    for text in ("เปิดงานโปสเตอร์โปรโมชันใหม่", "สร้างใบเสนอราคาให้ลูกค้า ABC", "เปิดหน้าร้านเกม", "open the games page", "เปิดหน้าคลังเกม"):
        assert client.post("/v1/cafe/assistant", json={"text": text}, headers=auth).json()["intent"] is None


def test_mira_offline_seat_is_not_proposed(cafe, client, auth):
    seat, dev = _ready(client, auth)
    with session_scope() as s:
        from app.cafe.models import CafeDevice

        s.get(CafeDevice, uuid.UUID(dev["device_id"])).last_heartbeat_at = None
    out = client.post("/v1/cafe/assistant", json={"text": "open Steam on seat 12", "language": "en"}, headers=auth).json()
    assert out["confirm"] is None and out["reply"] == "The PC at seat 12 is offline, so I can't open anything on it."


def test_mira_structured_tool_calling_with_model(cafe, client, auth):
    _ready(client, auth)
    cafe.openai_api_key = "sk-test"
    bodies = []

    def fake(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "Minecraft" in body["messages"][-1]["content"]:
            call = {"name": "run_shell", "arguments": json.dumps({"cmd": "format c:"})}  # a tool that does not exist
        else:
            call = {"name": "launch_app", "arguments": json.dumps({"app": "Steam", "seat": "12", "path": "C:\\x.exe"})}
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None,
                                                                  "tool_calls": [{"id": "c1", "type": "function", "function": call}]}}]})

    hybrid_router.TRANSPORTS["openai"] = httpx.MockTransport(fake)
    out = client.post("/v1/cafe/assistant", json={"text": "ขอเล่นสตีมหน่อยนะ ที่ 12", "use_ai": True}, headers=auth).json()
    assert out["tool_call"] == {"name": "launch_app", "arguments": {"app": "Steam", "seat": "12"}, "source": "model"}
    assert {t["function"]["name"] for t in bodies[0]["tools"]} >= {"launch_app", "get_remaining_time", "request_staff"}
    bad = client.post("/v1/cafe/assistant", json={"text": "เปิด Minecraft เกม ที่เครื่อง 12", "use_ai": True}, headers=auth).json()
    assert bad["tool_call"]["source"] == "rules" and bad["data"]["error"] == "APP_NOT_IN_CATALOG"


def test_production_requires_signing_key(monkeypatch):
    from app.config import Settings

    with pytest.raises(ValueError, match="CAFE_COMMAND_SIGNING_KEY"):
        Settings(environment="production", jwt_secret="x" * 40, secrets_encryption_key="k", database_url="postgresql://x/y",
                 celery_broker_url="redis://r", cafe_enabled=True)
