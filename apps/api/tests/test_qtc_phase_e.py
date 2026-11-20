"""Quote-to-Cash Phase E: scoped integration keys, signed outbound events, scans, dashboard, n8n workflow files."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tests.test_qtc_phase_b import _approved, _catalogue, _create, _login
from tests.test_qtc_phase_c import _issued, _order, _pay

WORKFLOWS = Path(__file__).resolve().parents[3] / "n8n" / "workflows"
NL = chr(10)
SECRET = "test-secret-0123456789-abcdefghijklmnop"  # noqa: S105 - test fixture


def _key(org_id, scopes):
    from app.db import session_scope
    from app.models import ApiKey
    from app.security import generate_api_key

    key, prefix, digest = generate_api_key()
    with session_scope() as session:
        session.add(ApiKey(organization_id=org_id, name="n8n-qtc", key_prefix=prefix, key_hash=digest, scopes=scopes))
    return {"X-API-Key": key}


def _events(event_type=None):
    from app.db import session_scope
    from app.qtc.models import QtcEvent

    with session_scope() as session:
        q = session.query(QtcEvent).order_by(QtcEvent.created_at)
        if event_type:
            q = q.filter(QtcEvent.event_type == event_type)
        return [{"type": e.event_type, "key": e.dedupe_key, "data": e.payload, "status": e.status, "attempts": e.attempts} for e in q]


def _run(fn, *args, **kw):
    from app.db import session_scope

    with session_scope() as session:
        return fn(session, *args, **kw)


class Receiver:
    """A fake n8n: records each delivery and answers with the next status code."""

    def __init__(self, codes=None):
        self.requests: list[httpx.Request] = []
        self.codes = list(codes or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.codes.pop(0) if self.codes else 202)


@pytest.fixture
def receiver(fresh_state, monkeypatch):
    from app.qtc import events

    fresh_state.qtc_events_url = "http://n8n.test/webhook/mcr-qtc"
    fresh_state.qtc_events_secret = SECRET
    fake = Receiver()
    monkeypatch.setattr(events, "TRANSPORT", httpx.MockTransport(fake))
    return fake


def _set_due(invoice_id, due):
    from app.db import session_scope
    from app.qtc.models import Invoice

    with session_scope() as session:
        session.get(Invoice, uuid.UUID(str(invoice_id))).due_date = due


# ---- scoped keys ----------------------------------------------------------------------------------
def test_keys_only_reach_what_their_scopes_allow(client, auth, org_a):
    legacy = {"X-API-Key": org_a["api_key"]}  # created before scopes: keeps jobs + Quote-to-Cash reads
    assert client.get("/v1/jobs", headers=legacy).status_code == 200
    assert client.get("/v1/qtc/customers", headers=legacy).status_code == 200
    assert client.post("/v1/qtc/agents/sales-intake", headers=legacy, json={"text": "ขอใบเสนอราคา"}).status_code == 403

    jobs_only = _key(org_a["org_id"], ["jobs"])
    assert client.get("/v1/jobs", headers=jobs_only).status_code == 200
    denied = client.get("/v1/qtc/dashboard", headers=jobs_only)
    assert denied.status_code == 403 and "qtc:read" in denied.json()["error"]["message"]

    qtc = _key(org_a["org_id"], ["qtc:read", "qtc:intake"])
    assert client.get("/v1/qtc/dashboard", headers=qtc).status_code == 200
    assert client.post("/v1/qtc/agents/sales-intake", headers=qtc, json={"text": "ขอใบเสนอราคางาน Production"}).status_code == 200
    assert client.get("/v1/jobs", headers=qtc).status_code == 403
    assert client.post("/v1/qtc/customers", headers=qtc, json={"name": "x"}).status_code == 403  # still read-only
    assert client.post("/v1/qtc/assistant/confirm", headers=qtc, json={"action": "draft_reminders", "payload": {}}).status_code == 403


def test_cli_refuses_unknown_scopes(capsys):
    from app.cli import create_api_key

    assert create_api_key("x", ["qtc:write"]) == 2
    assert "Unknown scope" in capsys.readouterr().err


# ---- events -----------------------------------------------------------------------------------------
def test_quotation_events_are_recorded_once_with_the_change(client, auth):
    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}])
    assert _events() == []  # a draft is not an event
    client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=auth, json={"decision": "changes_requested", "comment": "ลดราคา"})
    client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=auth, json={"decision": "approved"})
    for _ in range(2):  # the same acceptance recorded twice
        client.post(f"/v1/qtc/quotations/{q['id']}/accept", headers=auth, json={"accepted_by_name": "คุณเอ", "method": "email"})
    events = _events()
    assert [(e["type"], e["key"].rsplit(":", 1)[-1]) for e in events] == [
        ("quotation.submitted", "r1"),
        ("quotation.decided", "r1"),
        ("quotation.submitted", "r2"),
        ("quotation.decided", "r2"),
        ("quotation.accepted", q["id"]),
    ]
    sub = events[0]["data"]
    assert sub["number"] == q["number"] and sub["customer"] == "ABC" and sub["grand_total"] == "48150.00"
    assert sub["link"] == f"/qtc/quotations/{q['id']}"
    assert [e["data"]["decision"] for e in events if e["type"] == "quotation.decided"] == ["changes_requested", "approved"]
    text = json.dumps([e["data"] for e in events], ensure_ascii=False)
    assert "0105536000313" not in text  # the customer's tax ID stays in the app
    assert not {"email", "phone", "tax_id", "address"} & {k for e in events for k in e["data"]}  # no customer contact details


def test_events_are_delivered_signed_and_verifiable(client, auth, receiver):
    from app.qtc.events import deliver_pending, verify

    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}])
    client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    assert _run(deliver_pending) == {"delivered": 1, "retry": 0, "failed": 0}
    [req] = receiver.requests
    assert str(req.url) == "http://n8n.test/webhook/mcr-qtc-quotation-submitted"
    body = req.content.decode()
    ts = int(req.headers["x-mcr-timestamp"])
    assert verify(SECRET, ts, body, req.headers["x-mcr-signature"])
    assert not verify(SECRET, ts, body.replace("48150.00", "1.00"), req.headers["x-mcr-signature"])  # tampered
    assert not verify("another-secret-0123456789-abcdefghijkl", ts, body, req.headers["x-mcr-signature"])
    assert not verify(SECRET, ts, body, req.headers["x-mcr-signature"], now=ts + 301)  # replayed later
    envelope = json.loads(body)
    assert envelope["type"] == req.headers["x-mcr-event"] == "quotation.submitted" and envelope["id"] == req.headers["x-mcr-event-id"]
    assert envelope["data"]["number"] == q["number"]
    assert _events()[0]["status"] == "delivered"
    assert _run(deliver_pending)["delivered"] == 0  # never sent twice


def test_failed_deliveries_back_off_then_stop(client, auth, receiver):
    from app.db import session_scope
    from app.qtc import events
    from app.qtc.models import QtcEvent

    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}])
    client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    receiver.codes = [500] * events.MAX_ATTEMPTS
    assert _run(events.deliver_pending)["retry"] == 1
    assert _run(events.deliver_pending)["retry"] == 0  # not due yet: backoff
    for _ in range(events.MAX_ATTEMPTS - 1):
        with session_scope() as session:
            for e in session.query(QtcEvent):
                e.next_attempt_at = e.created_at
        _run(events.deliver_pending)
    [e] = _events()
    assert e["status"] == "failed" and e["attempts"] == events.MAX_ATTEMPTS and len(receiver.requests) == events.MAX_ATTEMPTS


def test_nothing_is_delivered_without_a_url_and_a_long_secret(client, auth, fresh_state, monkeypatch):
    from app.qtc import events

    fake = Receiver()
    monkeypatch.setattr(events, "TRANSPORT", httpx.MockTransport(fake))
    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}])
    client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    fresh_state.qtc_events_url, fresh_state.qtc_events_secret = "http://n8n.test/webhook/mcr-qtc", "short"
    assert _run(events.deliver_pending)["delivered"] == 0 and fake.requests == []
    assert _events()[0]["status"] == "pending"  # kept for when delivery is configured


def test_scan_emits_due_soon_overdue_and_weekly_summary_once(client, auth, org_b):
    from app.qtc.events import scan

    cat, q, order = _order(
        client,
        auth,
        schedule=[
            {"kind": "deposit", "description": "มัดจำ", "percent": "50"},
            {"kind": "final", "description": "งวดสุดท้าย", "percent": "50"},
        ],
    )
    soon, late = _issued(client, auth, order, 0), _issued(client, auth, order, 1)
    today = date.today()
    _set_due(soon["id"], today + timedelta(days=2))
    _set_due(late["id"], today - timedelta(days=5))
    first = _run(scan, today)
    assert first == {"invoice.due_soon": 1, "invoice.overdue": 1, "receivables.weekly_summary": 1}  # org B has no data
    assert _run(scan, today) == {"invoice.due_soon": 0, "invoice.overdue": 0, "receivables.weekly_summary": 0}
    [overdue] = _events("invoice.overdue")
    assert overdue["data"]["number"] == late["number"] and overdue["data"]["days_overdue"] == 5
    assert overdue["data"]["balance"] == "24075.00"
    [weekly] = _events("receivables.weekly_summary")
    assert weekly["data"]["outstanding"] == {"count": 2, "amount": "48150.00"} and weekly["data"]["overdue"]["amount"] == "24075.00"
    # a new due date is a new reminder; next week brings a new summary
    _set_due(late["id"], today - timedelta(days=1))
    assert _run(scan, today)["invoice.overdue"] == 1
    assert _run(scan, today + timedelta(days=7))["receivables.weekly_summary"] == 1


# ---- dashboard ----------------------------------------------------------------------------------------
def test_end_to_end_quote_to_cash_shows_exact_dashboard_figures(client, auth, org_a, org_b):
    empty = client.get("/v1/qtc/dashboard", headers=auth).json()
    assert empty["pipeline"] == {"count": 0, "amount": "0.00"} and empty["cash_received"]["amount"] == "0.00"

    cat = _catalogue(client, auth)
    waiting = _create(client, auth, cat, lines=[{"service_id": cat["edit"]["id"]}])  # 8000 + 7% = 8560.00
    client.post(f"/v1/qtc/quotations/{waiting['id']}/submit", headers=auth)
    expired = _approved(client, auth, cat)
    from app.db import session_scope
    from app.qtc.models import Quotation

    with session_scope() as session:
        session.get(Quotation, uuid.UUID(expired["id"])).valid_until = date.today() - timedelta(days=1)

    won = _approved(client, auth, cat)
    client.post(f"/v1/qtc/quotations/{won['id']}/accept", headers=auth, json={"accepted_by_name": "คุณเอ", "method": "purchase_order"})
    order = client.post(
        "/v1/qtc/orders",
        headers=auth,
        json={
            "quotation_id": won["id"],
            "schedule": [
                {"kind": "deposit", "description": "มัดจำ", "percent": "40"},
                {"kind": "final", "description": "ส่วนที่เหลือ", "percent": "60"},
            ],
        },
    ).json()
    deposit = _issued(client, auth, order, 0)  # 19260.00
    final = _issued(client, auth, order, 1)  # 28890.00
    _set_due(final["id"], date.today() - timedelta(days=3))
    p = _pay(client, auth, cat["customer"]["id"], "19260.00", [{"invoice_id": deposit["id"], "amount": "19260.00"}], reference="TR-1").json()
    unconfirmed = client.get("/v1/qtc/dashboard", headers=auth).json()
    assert unconfirmed["cash_received"]["amount"] == "0.00" and unconfirmed["outstanding"]["amount"] == "48150.00"
    client.post(f"/v1/qtc/payments/{p['id']}/confirm", headers=auth)

    d = client.get("/v1/qtc/dashboard", headers=auth).json()
    assert d["pipeline"] == {"count": 1, "amount": "8560.00"}  # the expired approved quotation is not pipeline
    assert d["awaiting_approval"] == {"count": 1, "amount": "8560.00"}
    assert d["accepted_this_month"] == {"count": 1, "amount": "48150.00"}
    assert d["outstanding"] == {"count": 1, "amount": "28890.00"}
    assert d["overdue"] == {"count": 1, "amount": "28890.00"}
    assert d["cash_received"] == {"count": 1, "amount": "19260.00"}
    # reconciliation: what was billed = what was received + what is still owed
    assert Decimal(d["cash_received"]["amount"]) + Decimal(d["outstanding"]["amount"]) == Decimal("48150.00")
    # Mira's receivables answer and the dashboard agree
    mira = client.post("/v1/qtc/assistant", headers=auth, json={"text": "สรุปยอดลูกหนี้เดือนนี้"}).json()["data"]["figures"]
    assert Decimal(mira["outstanding"]) == Decimal(d["outstanding"]["amount"]) and Decimal(mira["received_this_month"]) == Decimal("19260.00")

    other = client.get("/v1/qtc/dashboard", headers=_login(client, org_b["email"])).json()
    assert other["outstanding"]["amount"] == "0.00" and other["pipeline"]["count"] == 0  # tenant isolation


# ---- n8n workflow files -------------------------------------------------------------------------------
def _workflows():
    files = sorted(WORKFLOWS.glob("qtc-*.json"))
    assert len(files) == 7
    return {f.name: json.loads(f.read_text(encoding="utf-8")) for f in files}


def test_workflows_cover_every_event_and_embed_no_credentials():
    from app.qtc.events import EVENT_TYPES, slug

    flows = _workflows()
    text = json.dumps(flows, ensure_ascii=False)
    for needle in ("mcr_", "sk-", SECRET, "Bearer ", "password"):
        assert needle not in text
    paths = set()
    for name, wf in flows.items():
        for n in wf["nodes"]:
            if n["type"].endswith(".webhook"):
                paths.add(n["parameters"]["path"])
                signed = any(x["name"] == "Verify Signature" for x in wf["nodes"])
                assert signed or n["parameters"].get("authentication") == "headerAuth", name  # never an open webhook
            if n["type"].endswith(".httpRequest") and "MCR_API_URL" in n["parameters"]["url"]:
                assert n["credentials"]["httpHeaderAuth"]["name"] == "MCR QTC Key"  # referenced by name, not embedded
            for cred in (n.get("credentials") or {}).values():
                assert set(cred) == {"id", "name"} and cred["id"] == "REPLACE_AFTER_IMPORT"
    assert {f"mcr-qtc-{slug(t)}" for t in EVENT_TYPES} | {"mcr-qtc-inquiry"} == paths


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_n8n_signature_check_accepts_only_signed_events(tmp_path):
    """Runs the workflow's own Verify Signature JavaScript against the API's signer."""
    import time

    from app.qtc.events import sign

    verify_js = next(n for n in _workflows()["qtc-overdue-payment-alert.json"]["nodes"] if n["name"] == "Verify Signature")["parameters"]["jsCode"]
    body = json.dumps(
        {"id": "e1", "type": "invoice.overdue", "data": {"number": "INV-2026-0001", "customer": "ลูกค้า ทดสอบ"}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    ts = int(time.time())
    cases = {
        "good": (sign(SECRET, ts, body), ts, body),
        "tampered": (sign(SECRET, ts, body), ts, body.replace("0001", "0002")),
        "stale": (sign(SECRET, ts - 600, body), ts - 600, body),
        "wrong_type": (
            sign(SECRET, ts, body.replace("invoice.overdue", "invoice.due_soon")),
            ts,
            body.replace("invoice.overdue", "invoice.due_soon"),
        ),
    }
    script = tmp_path / "verify.js"
    script.write_text(
        "const cases = " + json.dumps({k: {"sig": v[0], "ts": v[1], "raw": v[2]} for k, v in cases.items()}, ensure_ascii=False) + ";\n"
        f"const $env = {{ MCR_EVENTS_SECRET: {json.dumps(SECRET)} }};\n"
        "(async () => { const out = {};\n"
        "for (const [name, c] of Object.entries(cases)) {\n"
        "  const $input = { first: () => ({ json: { headers: { 'x-mcr-timestamp': String(c.ts), 'x-mcr-signature': c.sig } },\n"
        "    binary: { data: {} } }) };\n"
        "  const ctx = { helpers: { getBinaryDataBuffer: async () => Buffer.from(c.raw, 'utf8') } };\n"
        "  const run = async function () {\n" + verify_js + "\n};\n"
        "  out[name] = (await run.call(ctx))[0].json.valid;\n"
        "}\nconsole.log(JSON.stringify(out)); })();\n",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout) == {"good": True, "tampered": False, "stale": False, "wrong_type": False}


@pytest.mark.skipif(not __import__("os").environ.get("TEST_DATABASE_URL"), reason="row locking needs PostgreSQL")
def test_parallel_delivery_workers_send_each_event_once(client, auth, receiver):
    from concurrent.futures import ThreadPoolExecutor

    from app.qtc.events import deliver_pending

    cat = _catalogue(client, auth)
    for _ in range(6):
        q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}])
        client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _run(deliver_pending), range(4)))
    ids = [r.headers["x-mcr-event-id"] for r in receiver.requests]
    assert sum(r["delivered"] for r in results) == 6 and len(ids) == len(set(ids)) == 6


def test_sales_intake_reads_labelled_inquiry_fields(client, auth, org_a):
    _catalogue(client, auth)
    key = _key(org_a["org_id"], ["qtc:intake"])
    text = "อยากได้ราคางานถ่ายโดรนและตัดต่อ" + NL + NL.join(["บริษัท: ร้านใหม่ จำกัด", "ผู้ติดต่อ: คุณบี", "อีเมล: b@example.com", "โทร: 081-234-5678"])
    r = client.post("/v1/qtc/agents/sales-intake", headers=key, json={"text": text}).json()
    assert r["customer"] is None
    assert r["new_customer"] == {"name": "ร้านใหม่ จำกัด", "email": "b@example.com", "phone": "081-234-5678", "contact_name": "คุณบี"}
    assert sorted(x["code"] for x in r["services"]) == ["DRONE", "EDIT"]
    known = client.post("/v1/qtc/agents/sales-intake", headers=key, json={"text": "ขอราคางาน Production" + NL + "บริษัท: ABC"}).json()
    assert known["customer"]["name"] == "ABC"
    assert client.get("/v1/qtc/customers", headers=auth).json()["total"] == 1  # read-only: nothing was created
