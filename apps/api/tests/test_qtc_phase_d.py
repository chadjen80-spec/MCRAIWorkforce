"""Quote-to-Cash Phase D: agent registry, QTC agents, Mira's structured intents, reminder drafts (never sent)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

import tests.conftest as conftest
from tests.test_qtc_phase_a import _member
from tests.test_qtc_phase_b import _catalogue, _login
from tests.test_qtc_phase_c import _issued, _order, _pay


def _ask(client, auth, text, language="th", **kw):
    r = client.post("/v1/qtc/assistant", headers=auth, json={"text": text, "language": language, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _make_overdue(invoice_id, days=10):
    from app.db import session_scope
    from app.qtc.models import Invoice

    with session_scope() as session:
        session.get(Invoice, uuid.UUID(str(invoice_id))).due_date = date.today() - timedelta(days=days)


def _overdue_invoice(client, auth, **kw):
    cat, q, order = _order(client, auth, **kw)
    inv = _issued(client, auth, order)
    _make_overdue(inv["id"])
    return cat, inv


# ---- registry -----------------------------------------------------------------------------------
def test_registry_keeps_the_six_creative_agents_and_adds_the_qtc_agents(client, auth):
    from app.agents import content, document, image, planner, qa, video
    from app.agents.registry import all_agents
    from app.worker.runner import AGENTS

    assert AGENTS == {"planner": planner.run, "content": content.run, "image": image.run, "document": document.run, "video": video.run, "qa": qa.run}
    assert {a.name: a.queue for a in all_agents("creative")} == {n: n for n in AGENTS}
    listed = client.get("/v1/qtc/agents", headers=auth).json()
    assert {a["name"] for a in listed if a["family"] == "qtc"} == {"sales_intake", "quotation", "finance", "collection"}


def test_registry_refuses_duplicate_names():
    from app.agents.registry import AgentSpec, register

    with pytest.raises(ValueError):
        register(AgentSpec("planner", "creative", "dup", None))


# ---- intents --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("สร้างใบเสนอราคางาน Production ให้ลูกค้า ABC", "create_quotation"),
        ("ตรวจสอบใบเสนอราคาที่ยังไม่อนุมัติ", "pending_quotations"),
        ("ลูกค้ารายไหนยังไม่จ่ายเงิน", "unpaid_customers"),
        ("สรุปยอดลูกหนี้เดือนนี้", "receivables_summary"),
        ("เตรียมข้อความติดตามเงินสำหรับ Invoice ที่เกินกำหนด", "collection_drafts"),
        ("Create a quotation for customer ABC", "create_quotation"),
        ("Which quotations are waiting for approval?", "pending_quotations"),
        ("Who hasn't paid yet?", "unpaid_customers"),
        ("Receivables summary this month", "receivables_summary"),
        ("Prepare payment reminders for overdue invoices", "collection_drafts"),
        # creative and other commands are left to Mira's existing flow
        ("ทำใบเสนอราคา PDF สำหรับแคมเปญโฆษณา", None),
        ("ทำโปสเตอร์โปรโมชันร้านกาแฟ", None),
        ("สร้างวิดีโอสั้น 30 วินาที แนะนำสินค้า", None),
        ("เขียนแคปชัน Facebook 3 โพสต์", None),
    ],
)
def test_commands_map_to_structured_intents(text, intent):
    from app.qtc.assistant_api import classify

    assert classify(text) == intent


def test_unmatched_text_returns_no_intent_and_writes_nothing(client, auth):
    out = _ask(client, auth, "ทำโปสเตอร์โปรโมชันร้านกาแฟ")
    assert out == {"intent": None, "agent": None, "reply": "", "data": {}, "confirm": None}


# ---- quotation proposals --------------------------------------------------------------------------
def test_quotation_proposal_prices_only_from_the_approved_list_and_writes_nothing(client, auth):
    cat = _catalogue(client, auth)
    out = _ask(client, auth, "สร้างใบเสนอราคางาน Production พร้อมถ่ายโดรน ให้ลูกค้า ABC")
    assert out["intent"] == "create_quotation" and out["agent"] == "quotation"
    lines = {x["description"]: x for x in out["data"]["lines"]}
    assert lines["งานโปรดักชั่นวิดีโอ"]["approved_price"] == "45000.0000"
    assert lines["ถ่ายโดรน"]["approved_price"] is None  # no approved price: never invented
    assert out["data"]["missing_prices"] == 1 and "ABC" in out["reply"]
    assert out["confirm"]["action"] == "create_quotation" and out["confirm"]["payload"]["customer_id"] == cat["customer"]["id"]
    assert client.get("/v1/qtc/quotations", headers=auth).json()["items"] == []  # a proposal, not a record

    done = client.post("/v1/qtc/assistant/confirm", headers=auth, json={**out["confirm"], "idempotency_key": "mira-1"})
    assert done.status_code == 200, done.text
    q = client.get(done.json()["data"]["link"].replace("/qtc/", "/v1/qtc/"), headers=auth).json()
    assert q["status"] == "draft" and q["missing_prices"] == 1
    assert {x["description"]: x["unit_price"] for x in q["lines"]} == {"งานโปรดักชั่นวิดีโอ": "45000.0000", "ถ่ายโดรน": None}
    again = client.post("/v1/qtc/assistant/confirm", headers=auth, json={**out["confirm"], "idempotency_key": "mira-1"})
    assert again.json()["data"]["quotation_id"] == done.json()["data"]["quotation_id"]  # confirming twice: one quotation


def test_quotation_proposal_explains_what_is_missing(client, auth):
    out = _ask(client, auth, "สร้างใบเสนอราคางาน Production ให้ลูกค้า Nobody")
    assert out["confirm"] is None and out["data"]["ok"] is False and out["data"]["link"] == "/qtc/customers"
    _catalogue(client, auth, approve=False)
    out = _ask(client, auth, "Create a quotation for customer ABC", language="en")
    assert out["confirm"] is None and out["data"]["link"] == "/qtc/services" and "approved price list" in out["reply"]


def test_ai_extra_items_are_descriptions_only(client, auth, fake_openai, monkeypatch):
    _catalogue(client, auth)
    real = conftest.llm_reply

    def reply(system, user):
        if "NOT in this catalogue" in system:
            return {"items": ["ไฟสตูดิโอ", "งานโปรดักชั่นวิดีโอ"], "unit_price": "99999"}  # an extra key is ignored
        return real(system, user)

    monkeypatch.setattr(conftest, "llm_reply", reply)
    out = _ask(client, auth, "สร้างใบเสนอราคางาน Production ให้ลูกค้า ABC", use_ai=True)
    assert out["data"]["used_ai"] is True
    assert [(x["description"], x["approved_price"]) for x in out["data"]["lines"]] == [("งานโปรดักชั่นวิดีโอ", "45000.0000"), ("ไฟสตูดิโอ", None)]
    from app.db import session_scope
    from app.models import UsageEvent

    with session_scope() as session:
        assert [u.agent for u in session.query(UsageEvent).all()] == ["quotation"]  # the call was costed


def test_ai_is_not_called_unless_asked(client, auth, fake_openai):
    _catalogue(client, auth)
    _ask(client, auth, "สร้างใบเสนอราคางาน Production ให้ลูกค้า ABC")
    assert fake_openai.calls == []


# ---- finance answers --------------------------------------------------------------------------------
def test_finance_answers_come_from_exact_figures(client, auth):
    assert _ask(client, auth, "ลูกค้ารายไหนยังไม่จ่ายเงิน")["data"]["items"] == []
    cat, inv = _overdue_invoice(client, auth)  # deposit 50% of 48,150.00
    p = _pay(client, auth, cat["customer"]["id"], "1000.10", [{"invoice_id": inv["id"], "amount": "1000.10"}]).json()
    unconfirmed = _ask(client, auth, "ลูกค้ารายไหนยังไม่จ่ายเงิน")["data"]["items"][0]
    assert unconfirmed["outstanding"] == inv["total"]  # a payment counts only once a person confirms it
    assert client.post(f"/v1/qtc/payments/{p['id']}/confirm", headers=auth).status_code == 200
    balance = Decimal(inv["total"]) - Decimal("1000.10")

    unpaid = _ask(client, auth, "ลูกค้ารายไหนยังไม่จ่ายเงิน")
    assert unpaid["data"]["items"] == [
        {"customer": "ABC", "code": cat["customer"]["code"], "outstanding": str(balance), "overdue": str(balance), "invoices": 1}
    ]
    assert f"{balance:,.2f}" in unpaid["reply"]

    figures = _ask(client, auth, "สรุปยอดลูกหนี้เดือนนี้")["data"]["figures"]
    assert figures["outstanding"] == figures["overdue"] == str(balance) and figures["open_invoices"] == 1
    assert figures["invoiced_this_month"] == inv["total"] and figures["received_this_month"] == "1000.10"


def test_pending_quotations(client, auth):
    cat = _catalogue(client, auth)
    q = client.post(
        "/v1/qtc/quotations", headers=auth, json={"customer_id": cat["customer"]["id"], "lines": [{"service_id": cat["prod"]["id"]}]}
    ).json()
    assert _ask(client, auth, "ตรวจสอบใบเสนอราคาที่ยังไม่อนุมัติ")["data"]["items"] == []
    client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    out = _ask(client, auth, "ตรวจสอบใบเสนอราคาที่ยังไม่อนุมัติ")
    assert [x["number"] for x in out["data"]["items"]] == [q["number"]] and "48,150.00" in out["reply"]


# ---- collection ---------------------------------------------------------------------------------------
def test_reminders_are_drafts_on_consented_channels_and_are_never_sent(client, auth, org_a):
    cat, inv = _overdue_invoice(client, auth)
    out = _ask(client, auth, "เตรียมข้อความติดตามเงินสำหรับ Invoice ที่เกินกำหนด")
    assert out["intent"] == "collection_drafts" and [x["number"] for x in out["data"]["items"]] == [inv["number"]]
    assert client.get("/v1/qtc/reminders", headers=auth).json() == []  # nothing until confirmed

    done = client.post("/v1/qtc/assistant/confirm", headers=auth, json=out["confirm"]).json()
    rid = done["data"]["reminder_ids"][0]
    [r] = client.get("/v1/qtc/reminders", headers=auth).json()
    assert r["id"] == rid and r["status"] == "draft" and r["channel"] == "manual"  # no contact consent on file
    assert inv["number"] in r["body"] and f"{Decimal(inv['total']):,.2f}" in r["body"]

    member = _member(client, org_a["org_id"])
    assert client.post(f"/v1/qtc/reminders/{rid}/approve", headers=member).status_code == 403  # owner/admin only
    approved = client.post(f"/v1/qtc/reminders/{rid}/approve", headers=auth)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    sent = client.post(f"/v1/qtc/reminders/{rid}/send", headers=auth)
    assert sent.status_code == 403 and sent.json()["error"]["code"] == "EXTERNAL_SENDING_DISABLED"
    assert client.patch(f"/v1/qtc/reminders/{rid}", headers=auth, json={"body": "x"}).status_code == 409


def test_reminder_channel_follows_contact_consent(client, auth, org_a):
    import uuid

    from app.db import session_scope
    from app.qtc.models import CustomerContact

    cat, inv = _overdue_invoice(client, auth)
    with session_scope() as session:
        cid = uuid.UUID(cat["customer"]["id"])
        session.add(CustomerContact(organization_id=org_a["org_id"], customer_id=cid, name="คุณซี", line_id="abc", consent_line=True))
        session.add(
            CustomerContact(organization_id=org_a["org_id"], customer_id=cid, name="คุณบี", email="b@example.com", is_primary=True, consent_email=False)
        )
    out = _ask(client, auth, "Prepare payment reminders for overdue invoices", language="en")
    client.post("/v1/qtc/assistant/confirm", headers=auth, json={**out["confirm"], "language": "en"})
    [r] = client.get("/v1/qtc/reminders", headers=auth).json()
    assert r["channel"] == "manual"  # the primary contact has an email but did not agree to email
    assert r["body"].startswith("Dear ")


def test_reminder_text_validation():
    from app.qtc.agents import reminder_text_ok

    bal = Decimal("4926.66")
    assert reminder_text_ok("ใบแจ้งหนี้ INV-2026-0001 ยอดคงค้าง 4,926.66 บาท ครบกำหนด 2026-11-01", "INV-2026-0001", bal)
    assert not reminder_text_ok("ใบแจ้งหนี้ INV-2026-0001 ยอด 5,000.00 บาท", "INV-2026-0001", bal)  # a different amount
    assert not reminder_text_ok("ใบแจ้งหนี้ INV-2026-0001 ยอด 4,926.66 บาท ส่วนลด 100.00", "INV-2026-0001", bal)
    assert not reminder_text_ok("ยอดคงค้าง 4,926.66 บาท", "INV-2026-0001", bal)  # invoice not named
    assert not reminder_text_ok("ใบแจ้งหนี้ INV-2026-0001 กรุณาชำระ", "INV-2026-0001", bal)  # no amount at all
    assert reminder_text_ok("Invoice INV-1 balance 5,000 baht", "INV-1", Decimal("5000.00"))


def test_collection_draft_with_ai_validates_the_text(client, auth, org_a, fake_openai, monkeypatch):
    from app.db import session_scope
    from app.models import Organization
    from app.qtc.agents import CollectionAgent, QtcAgentContext

    cat, inv = _overdue_invoice(client, auth)
    balance = Decimal(inv["total"])
    real = conftest.llm_reply
    texts = iter([f"ใบแจ้งหนี้ {inv['number']} ยอด 1,000.00 บาท", f"ใบแจ้งหนี้ {inv['number']} ยอดคงค้าง {balance:,.2f} บาท ขอบคุณค่ะ"])

    def reply(system, user):
        return {"text": next(texts)} if "payment reminder" in system else real(system, user)

    monkeypatch.setattr(conftest, "llm_reply", reply)
    import uuid

    with session_scope() as session:
        org = session.get(Organization, org_a["org_id"])
        ctx = QtcAgentContext(session=session, org=org, actor="test", use_ai=True)
        bad = CollectionAgent(ctx).draft([uuid.UUID(inv["id"])])[0]
        good = CollectionAgent(ctx).draft([uuid.UUID(inv["id"])])[0]
        assert bad.body.startswith("เรียน ")  # invalid AI text discarded, template used
        assert good.body.startswith("ใบแจ้งหนี้ ") and f"{balance:,.2f}" in good.body


# ---- permissions and tenancy ----------------------------------------------------------------------
def test_confirm_needs_a_person_and_answers_stay_inside_the_organization(client, auth, org_b):
    _overdue_invoice(client, auth)
    auth_b = _login(client, org_b["email"])
    assert _ask(client, auth_b, "ลูกค้ารายไหนยังไม่จ่ายเงิน")["data"]["items"] == []
    assert _ask(client, auth_b, "เตรียมข้อความติดตามเงินสำหรับ Invoice ที่เกินกำหนด")["confirm"] is None
    figures = _ask(client, auth_b, "สรุปยอดลูกหนี้เดือนนี้")["data"]["figures"]
    assert Decimal(figures["outstanding"]) == 0 and figures["open_invoices"] == 0

    # org B cannot draft reminders for org A's invoices even with their ids
    out_a = _ask(client, auth, "เตรียมข้อความติดตามเงินสำหรับ Invoice ที่เกินกำหนด")
    r = client.post("/v1/qtc/assistant/confirm", headers=auth_b, json=out_a["confirm"])
    assert r.status_code == 200 and r.json()["data"]["reminder_ids"] == []
    rid = client.post("/v1/qtc/assistant/confirm", headers=auth, json=out_a["confirm"]).json()["data"]["reminder_ids"][0]
    assert client.post(f"/v1/qtc/reminders/{rid}/approve", headers=auth_b).status_code == 404
    assert client.get("/v1/qtc/reminders", headers=auth_b).json() == []
