"""Quote-to-Cash Phase C: sales orders, billing schedule, invoices, payments, receivables, reconciliation."""

from __future__ import annotations

import io
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

from tests.test_qtc_phase_a import _member
from tests.test_qtc_phase_b import _approved, _catalogue, _login


def _accepted(client, auth, **kw):
    cat = _catalogue(client, auth)
    q = _approved(client, auth, cat, **kw)  # one PROD line: 45000 + 7% = 48150.00
    r = client.post(f"/v1/qtc/quotations/{q['id']}/accept", headers=auth, json={"accepted_by_name": "คุณเอ", "method": "purchase_order"})
    assert r.status_code == 200, r.text
    return cat, r.json()


def _order(client, auth, schedule=None, **kw):
    cat, q = _accepted(client, auth, **kw)
    body = {"quotation_id": q["id"]}
    if schedule is not None:
        body["schedule"] = schedule
    r = client.post("/v1/qtc/orders", headers=auth, json=body)
    assert r.status_code == 201, r.text
    return cat, q, r.json()


def _issued(client, auth, order, milestone_index=0):
    m = [x for x in order["milestones"] if x["kind"] != "delivery"][milestone_index]
    inv = client.post("/v1/qtc/invoices", headers=auth, json={"order_id": order["id"], "milestone_id": m["id"]}).json()
    r = client.post(f"/v1/qtc/invoices/{inv['id']}/issue", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def _pay(client, auth, customer_id, amount, allocations, reference="", headers=None):
    return client.post(
        "/v1/qtc/payments",
        headers={**auth, **(headers or {})},
        json={
            "customer_id": customer_id,
            "amount": amount,
            "received_on": date.today().isoformat(),
            "method": "bank_transfer",
            "reference": reference,
            "allocations": allocations,
        },
    )


# ---- orders ------------------------------------------------------------------------------------
def test_accepted_quotation_becomes_an_order_with_an_exact_schedule(client, auth):
    cat, q, order = _order(
        client,
        auth,
        schedule=[
            {"kind": "deposit", "description": "มัดจำ 30%", "percent": "30"},
            {"kind": "final", "description": "ส่วนที่เหลือเมื่อส่งมอบ", "percent": "70"},
        ],
    )
    assert order["number"] == f"SO-{date.today().year}-0001" and order["quotation_revision"] == 1 and order["status"] == "open"
    assert order["grand_total"] == "48150.00" and order["lines"][0]["unit_price"] == "45000.0000"
    billing = [m for m in order["milestones"] if m["kind"] != "delivery"]
    assert [(m["amount_total"], m["amount_vat"], m["amount_net"]) for m in billing] == [
        ("14445.00", "945.00", "13500.00"),
        ("33705.00", "2205.00", "31500.00"),
    ]
    assert sum(Decimal(m["amount_total"]) for m in billing) == Decimal(order["grand_total"])
    assert [m["description"] for m in order["milestones"] if m["kind"] == "delivery"] == ["ส่งมอบ: งานโปรดักชั่นวิดีโอ"]


def test_rounding_remainder_goes_to_the_last_installment(client, auth):
    _, _, order = _order(
        client,
        auth,
        schedule=[{"kind": "installment", "description": f"งวด {i}", "percent": p} for i, p in ((1, "33.33"), (2, "33.33"), (3, "33.34"))],
    )
    parts = [m for m in order["milestones"] if m["kind"] != "delivery"]
    assert [m["amount_total"] for m in parts] == ["16048.40", "16048.40", "16053.20"]
    assert sum(Decimal(m["amount_total"]) for m in parts) == Decimal("48150.00")
    assert sum(Decimal(m["amount_vat"]) for m in parts) == Decimal("3150.00")


def test_schedule_must_add_up(client, auth):
    _, q = _accepted(client, auth)
    for schedule, code in (
        ([{"kind": "deposit", "description": "x", "percent": "30"}], "SCHEDULE_MISMATCH"),
        ([{"kind": "final", "description": "x", "amount": "48150.01"}], "SCHEDULE_MISMATCH"),
        ([{"kind": "deposit", "description": "x", "percent": "50"}, {"kind": "final", "description": "y", "amount": "24075"}], "VALIDATION_ERROR"),
    ):
        r = client.post("/v1/qtc/orders", headers=auth, json={"quotation_id": q["id"], "schedule": schedule})
        assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text


def test_only_accepted_quotations_convert_and_only_once(client, auth):
    cat = _catalogue(client, auth)
    q = _approved(client, auth, cat)
    r = client.post("/v1/qtc/orders", headers=auth, json={"quotation_id": q["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ACCEPTED"
    client.post(f"/v1/qtc/quotations/{q['id']}/accept", headers=auth, json={"accepted_by_name": "x", "method": "email"})
    assert client.post("/v1/qtc/orders", headers=auth, json={"quotation_id": q["id"]}).status_code == 201
    again = client.post("/v1/qtc/orders", headers=auth, json={"quotation_id": q["id"]})
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_CONVERTED" and again.json()["error"]["details"]["order_id"]


def test_parallel_conversion_creates_one_order(client, auth):
    _, q = _accepted(client, auth)

    def convert(_):
        return client.post("/v1/qtc/orders", headers=auth, json={"quotation_id": q["id"]}).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = sorted(pool.map(convert, range(4)))
    assert codes.count(201) == 1 and codes.count(409) == 3
    assert client.get("/v1/qtc/orders", headers=auth).json()["total"] == 1


# ---- invoices -----------------------------------------------------------------------------------
def test_invoices_are_numbered_on_issue_and_one_per_milestone(client, auth, org_a):
    _, _, order = _order(
        client,
        auth,
        schedule=[{"kind": "deposit", "description": "มัดจำ", "percent": "30"}, {"kind": "final", "description": "งวดสุดท้าย", "percent": "70"}],
    )
    deposit = order["milestones"][0]
    draft = client.post("/v1/qtc/invoices", headers=auth, json={"order_id": order["id"], "milestone_id": deposit["id"]}).json()
    assert draft["status"] == "draft" and draft["number"] is None and draft["total"] == "14445.00"
    assert draft["lines"][0]["description"].startswith("มัดจำ ตามใบสั่งขาย SO-")
    dup = client.post("/v1/qtc/invoices", headers=auth, json={"order_id": order["id"], "milestone_id": deposit["id"]})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "ALREADY_INVOICED"
    issued = client.post(f"/v1/qtc/invoices/{draft['id']}/issue", headers=auth).json()
    assert issued["number"] == f"INV-{date.today().year}-0001" and issued["status"] == "issued"
    assert issued["due_date"] == (date.today() + timedelta(days=30)).isoformat()  # customer's payment terms
    assert client.post(f"/v1/qtc/invoices/{draft['id']}/issue", headers=auth).status_code == 409
    # a commercial invoice, clearly not a tax invoice; tax documents stay disabled
    r = client.post(f"/v1/qtc/invoices/{draft['id']}/tax-invoice", headers=auth)
    assert r.status_code == 403 and r.json()["error"]["code"] == "TAX_DOCUMENTS_DISABLED"
    doc = client.get(f"/v1/qtc/invoices/{draft['id']}/document", headers={"X-API-Key": org_a["api_key"]}, params={"format": "docx"})
    with zipfile.ZipFile(io.BytesIO(doc.content)) as z:
        text = z.read("word/document.xml").decode("utf-8")
    assert "ใบแจ้งหนี้" in text and "ไม่ใช่ใบกำกับภาษี" in text and issued["number"] in text and "ฉบับร่าง" not in text
    assert client.get(f"/v1/qtc/invoices/{draft['id']}/document", headers=auth, params={"format": "pdf"}).content.startswith(b"%PDF")


def test_whole_order_invoice_copies_the_order_lines(client, auth):
    _, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    assert inv["total"] == "48150.00" and [x["description"] for x in inv["lines"]] == ["งานโปรดักชั่นวิดีโอ"]


def test_draft_invoices_are_not_given_to_integrations(client, auth, org_a):
    _, _, order = _order(client, auth)
    m = order["milestones"][0]
    draft = client.post("/v1/qtc/invoices", headers=auth, json={"order_id": order["id"], "milestone_id": m["id"]}).json()
    assert client.get(f"/v1/qtc/invoices/{draft['id']}/document", headers={"X-API-Key": org_a["api_key"]}).status_code == 403
    with zipfile.ZipFile(io.BytesIO(client.get(f"/v1/qtc/invoices/{draft['id']}/document", headers=auth, params={"format": "docx"}).content)) as z:
        assert "ฉบับร่าง" in z.read("word/document.xml").decode("utf-8")


# ---- payments ------------------------------------------------------------------------------------
def test_partial_and_full_payment_need_confirmation(client, auth, org_a):
    cat, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    cust = cat["customer"]["id"]
    p1 = _pay(client, auth, cust, "20000", [{"invoice_id": inv["id"], "amount": "20000"}], reference="TRX-001").json()
    assert p1["status"] == "pending_confirmation"
    assert client.get(f"/v1/qtc/invoices/{inv['id']}", headers=auth).json()["paid_amount"] == "0.00"  # nothing applied yet
    member = _member(client, org_a["org_id"])
    assert client.post(f"/v1/qtc/payments/{p1['id']}/confirm", headers=member).status_code == 403
    assert client.post(f"/v1/qtc/payments/{p1['id']}/confirm", headers={"X-API-Key": org_a["api_key"]}).status_code == 403
    assert client.post(f"/v1/qtc/payments/{p1['id']}/confirm", headers=auth).json()["status"] == "confirmed"
    after = client.get(f"/v1/qtc/invoices/{inv['id']}", headers=auth).json()
    assert (after["status"], after["paid_amount"], after["balance"]) == ("partially_paid", "20000.00", "28150.00")
    p2 = _pay(client, auth, cust, "28150", [{"invoice_id": inv["id"], "amount": "28150"}], reference="TRX-002").json()
    client.post(f"/v1/qtc/payments/{p2['id']}/confirm", headers=auth)
    done = client.get(f"/v1/qtc/invoices/{inv['id']}", headers=auth).json()
    assert done["status"] == "paid" and done["balance"] == "0.00"
    assert client.get(f"/v1/qtc/orders/{order['id']}", headers=auth).json()["status"] == "completed"


def test_overpayments_are_refused_or_kept_as_credit(client, auth):
    cat, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    cust = cat["customer"]["id"]
    r = _pay(client, auth, cust, "50000", [{"invoice_id": inv["id"], "amount": "48150.01"}])
    assert r.status_code == 422 and r.json()["error"]["code"] == "OVERPAYMENT"
    assert _pay(client, auth, cust, "100", [{"invoice_id": inv["id"], "amount": "101"}]).json()["error"]["code"] == "OVERALLOCATED"
    p = _pay(client, auth, cust, "50000", [{"invoice_id": inv["id"], "amount": "48150"}], reference="TRX-OVER").json()
    assert p["unallocated"] == "1850.00"
    client.post(f"/v1/qtc/payments/{p['id']}/confirm", headers=auth)
    rec = client.get("/v1/qtc/receivables", headers=auth).json()
    assert rec["total_outstanding"] == "0.00" and rec["items"][0]["unapplied_credit"] == "1850.00"


def test_concurrent_confirmations_cannot_overpay(client, auth):
    cat, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    cust = cat["customer"]["id"]
    a = _pay(client, auth, cust, "30000", [{"invoice_id": inv["id"], "amount": "30000"}], reference="A").json()
    b = _pay(client, auth, cust, "30000", [{"invoice_id": inv["id"], "amount": "30000"}], reference="B").json()  # each fits alone

    def confirm(pid):
        return client.post(f"/v1/qtc/payments/{pid}/confirm", headers=auth).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(confirm, [a["id"], b["id"]]))
    assert codes == [200, 409]
    final = client.get(f"/v1/qtc/invoices/{inv['id']}", headers=auth).json()
    assert final["paid_amount"] == "30000.00" and final["status"] == "partially_paid"


def test_duplicate_payment_submissions(client, auth):
    cat, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    cust = cat["customer"]["id"]
    alloc = [{"invoice_id": inv["id"], "amount": "1000"}]
    first = _pay(client, auth, cust, "1000", alloc, reference="KBANK-123", headers={"Idempotency-Key": "slip-1"})
    again = _pay(client, auth, cust, "1000", alloc, reference="KBANK-123", headers={"Idempotency-Key": "slip-1"})
    assert (first.status_code, again.status_code) == (201, 200) and first.json()["id"] == again.json()["id"]
    dup = _pay(client, auth, cust, "1000", alloc, reference="KBANK-123")
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "DUPLICATE_PAYMENT"
    client.post(f"/v1/qtc/payments/{first.json()['id']}/reject", headers=auth, json={"reason": "wrong slip"})
    assert _pay(client, auth, cust, "1000", alloc, reference="KBANK-123").status_code == 201  # a rejected entry does not block

    p = _pay(client, auth, cust, "500", [{"invoice_id": inv["id"], "amount": "500"}], reference="K-2").json()
    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = sorted(pool.map(lambda _: client.post(f"/v1/qtc/payments/{p['id']}/confirm", headers=auth).status_code, range(3)))
    assert codes.count(200) == 1
    assert client.get(f"/v1/qtc/invoices/{inv['id']}", headers=auth).json()["paid_amount"] == "500.00"  # applied once


def test_evidence_never_confirms_a_payment(client, auth, org_a):
    cat, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    p = _pay(client, auth, cat["customer"]["id"], "1000", [{"invoice_id": inv["id"], "amount": "1000"}], reference="SLIP").json()
    slip = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = client.post(f"/v1/qtc/payments/{p['id']}/evidence", headers=auth, files={"file": ("slip.png", slip, "image/png")})
    assert r.status_code == 200 and r.json()["has_evidence"] and r.json()["status"] == "pending_confirmation"
    assert client.get(f"/v1/qtc/invoices/{inv['id']}", headers=auth).json()["paid_amount"] == "0.00"
    assert client.get(f"/v1/qtc/payments/{p['id']}/evidence", headers=auth).content == slip
    assert client.get(f"/v1/qtc/payments/{p['id']}/evidence", headers={"X-API-Key": org_a["api_key"]}).status_code == 403
    bad = client.post(f"/v1/qtc/payments/{p['id']}/evidence", headers=auth, files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert bad.status_code == 415


# ---- cancellation ------------------------------------------------------------------------------
def test_invoice_cancellation_rules(client, auth, org_a):
    cat, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    member = _member(client, org_a["org_id"])
    assert client.post(f"/v1/qtc/invoices/{inv['id']}/cancel", headers=member, json={"reason": "x"}).status_code == 403
    p = _pay(client, auth, cat["customer"]["id"], "100", [{"invoice_id": inv["id"], "amount": "100"}], reference="P1").json()
    assert client.post(f"/v1/qtc/invoices/{inv['id']}/cancel", headers=auth, json={}).json()["error"]["code"] == "HAS_PENDING_PAYMENTS"
    client.post(f"/v1/qtc/payments/{p['id']}/confirm", headers=auth)
    assert client.post(f"/v1/qtc/invoices/{inv['id']}/cancel", headers=auth, json={}).json()["error"]["code"] == "HAS_PAYMENTS"

    q2 = _approved(client, auth, cat)  # a second order from the same catalogue
    client.post(f"/v1/qtc/quotations/{q2['id']}/accept", headers=auth, json={"accepted_by_name": "x", "method": "email"})
    order2 = client.post("/v1/qtc/orders", headers=auth, json={"quotation_id": q2["id"]}).json()
    inv2 = _issued(client, auth, order2)
    c = client.post(f"/v1/qtc/invoices/{inv2['id']}/cancel", headers=auth, json={"reason": "ผิดงวด"}).json()
    assert c["status"] == "cancelled" and c["number"] == inv2["number"]  # the number is kept, never reused
    again = client.post("/v1/qtc/invoices", headers=auth, json={"order_id": order2["id"], "milestone_id": order2["milestones"][0]["id"]})
    assert again.status_code == 201  # the milestone can be invoiced again
    reissued = client.post(f"/v1/qtc/invoices/{again.json()['id']}/issue", headers=auth).json()
    assert reissued["number"] != inv2["number"]


def test_overdue_invoices(client, auth):
    from app.db import session_scope
    from app.qtc.models import Invoice

    cat, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    with session_scope() as session:
        session.get(Invoice, uuid.UUID(inv["id"])).due_date = date.today() - timedelta(days=3)
    assert client.get(f"/v1/qtc/invoices/{inv['id']}", headers=auth).json()["status"] == "overdue"
    assert client.get("/v1/qtc/invoices", headers=auth, params={"status": "overdue"}).json()["total"] == 1
    rec = client.get("/v1/qtc/receivables", headers=auth).json()
    assert rec["total_overdue"] == "48150.00" and rec["items"][0]["oldest_due"] == (date.today() - timedelta(days=3)).isoformat()


# ---- reconciliation and isolation -----------------------------------------------------------------
def test_financial_reconciliation(client, auth):
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.qtc.models import Invoice, PaymentAllocation, PaymentRecord

    cat, _, order = _order(
        client,
        auth,
        schedule=[{"kind": "deposit", "description": "มัดจำ", "percent": "40"}, {"kind": "final", "description": "งวดสุดท้าย", "percent": "60"}],
    )
    i1 = _issued(client, auth, order, 0)
    i2 = _issued(client, auth, order, 1)
    cust = cat["customer"]["id"]
    for amount, allocs, ref, ok in (
        ("10000", [(i1, "10000")], "R1", True),
        ("30000", [(i1, "9260"), (i2, "20000")], "R2", True),
        ("5000", [(i2, "5000")], "R3", False),
    ):
        p = _pay(client, auth, cust, amount, [{"invoice_id": i["id"], "amount": a} for i, a in allocs], reference=ref).json()
        client.post(f"/v1/qtc/payments/{p['id']}/{'confirm' if ok else 'reject'}", headers=auth, json={} if not ok else None)
    with session_scope() as s:
        for inv in s.scalars(select(Invoice)):
            confirmed = s.scalar(
                select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                .join(PaymentRecord)
                .where(PaymentAllocation.invoice_id == inv.id, PaymentRecord.status == "confirmed")
            )
            assert inv.paid_amount == Decimal(confirmed)  # every baht on an invoice comes from a confirmed allocation
        received = s.scalar(select(func.sum(PaymentRecord.amount)).where(PaymentRecord.status == "confirmed"))
        applied = s.scalar(select(func.sum(PaymentAllocation.amount)).join(PaymentRecord).where(PaymentRecord.status == "confirmed"))
    rec = client.get("/v1/qtc/receivables", headers=auth).json()
    assert Decimal(received) == Decimal(applied) + Decimal(rec["items"][0]["unapplied_credit"])  # 40000 = 39260 + 740
    assert Decimal(rec["total_outstanding"]) == Decimal(order["grand_total"]) - Decimal(applied)
    assert client.get(f"/v1/qtc/invoices/{i1['id']}", headers=auth).json()["status"] == "paid"


def test_tenant_isolation(client, auth, org_b):
    cat, _, order = _order(client, auth)
    inv = _issued(client, auth, order)
    other = _login(client, org_b["email"])
    for path in (f"/v1/qtc/orders/{order['id']}", f"/v1/qtc/invoices/{inv['id']}", f"/v1/qtc/invoices/{inv['id']}/document"):
        assert client.get(path, headers=other).status_code == 404
    assert (
        client.post("/v1/qtc/invoices", headers=other, json={"order_id": order["id"], "milestone_id": order["milestones"][0]["id"]}).status_code
        == 404
    )
    theirs = client.post("/v1/qtc/customers", headers=other, json={"name": "Theirs"}).json()
    r = _pay(client, other, theirs["id"], "100", [{"invoice_id": inv["id"], "amount": "100"}])
    assert r.status_code == 404  # cannot pay into another organization's invoice
    assert client.get("/v1/qtc/receivables", headers=other).json()["items"] == []
