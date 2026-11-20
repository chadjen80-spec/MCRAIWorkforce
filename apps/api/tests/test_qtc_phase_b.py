"""Quote-to-Cash Phase B: quotation engine, approval, revisions, acceptance, Thai PDF / DOCX."""
from __future__ import annotations

import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.qtc.quotations import thai_baht_text
from tests.test_qtc_phase_a import _member


def _login(client, email):
    from tests.conftest import PASSWORD

    return {"Authorization": "Bearer " + client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]}


def _catalogue(client, auth, *, inclusive=False, approve=True):
    prod = client.post("/v1/qtc/services", headers=auth, json={"code": "PROD", "name": "งานโปรดักชั่นวิดีโอ", "unit": "งาน",
                                                              "keywords": ["production", "โปรดักชั่น"]}).json()
    edit = client.post("/v1/qtc/services", headers=auth, json={"code": "EDIT", "name": "ตัดต่อ", "unit": "ชิ้น", "keywords": ["ตัดต่อ"]}).json()
    unpriced = client.post("/v1/qtc/services", headers=auth, json={"code": "DRONE", "name": "ถ่ายโดรน", "keywords": ["โดรน"]}).json()
    pl = client.post("/v1/qtc/price-lists", headers=auth, json={"name": "Rates", "vat_rate": "0.07", "prices_include_vat": inclusive}).json()
    client.put(f"/v1/qtc/price-lists/{pl['id']}/items/{prod['id']}", headers=auth, json={"unit_price": "45000"})
    client.put(f"/v1/qtc/price-lists/{pl['id']}/items/{edit['id']}", headers=auth, json={"unit_price": "8000"})
    if approve:
        assert client.post(f"/v1/qtc/price-lists/{pl['id']}/activate", headers=auth).status_code == 200
    customer = client.post("/v1/qtc/customers", headers=auth, json={"name": "ABC", "legal_name": "บริษัท เอบีซี จำกัด",
                                                                     "tax_id": "0105536000313", "address": "กรุงเทพฯ"}).json()
    return {"prod": prod, "edit": edit, "unpriced": unpriced, "pl": pl, "customer": customer}


def _create(client, auth, cat, **body):
    r = client.post("/v1/qtc/quotations", headers=auth, json={"customer_id": cat["customer"]["id"], **body})
    assert r.status_code == 201, r.text
    return r.json()


def _approved(client, auth, cat, **body):
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}], **body)
    assert client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth).status_code == 200
    r = client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=auth, json={"decision": "approved"})
    assert r.status_code == 200, r.text
    return r.json()


# ---- pricing ---------------------------------------------------------------------------------
def test_brief_is_matched_to_services_and_priced_only_from_the_approved_list(client, auth):
    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, brief="ขอใบเสนอราคางาน Production 1 งาน พร้อมตัดต่อ และถ่ายโดรน")
    assert q["number"] == f"QT-{date.today().year}-0001" and q["status"] == "draft"
    assert [m["code"] for m in q["matched"]] == ["DRONE", "EDIT", "PROD"]
    by_desc = {x["description"]: x for x in q["lines"]}
    assert by_desc["งานโปรดักชั่นวิดีโอ"]["unit_price"] == "45000.0000" and by_desc["งานโปรดักชั่นวิดีโอ"]["price_source"] == "price_list"
    assert by_desc["ถ่ายโดรน"]["unit_price"] is None and by_desc["ถ่ายโดรน"]["price_source"] == "missing"  # never invented
    assert q["missing_prices"] == 1
    assert q["subtotal"] == "53000.00" and q["vat_total"] == "3710.00" and q["grand_total"] == "56710.00"
    assert q["amount_in_words"] == "ห้าหมื่นหกพันเจ็ดร้อยสิบบาทถ้วน"


def test_quotations_need_an_approved_price_list(client, auth):
    cat = _catalogue(client, auth, approve=False)
    r = client.post("/v1/qtc/quotations", headers=auth, json={"customer_id": cat["customer"]["id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_APPROVED_PRICE_LIST"
    r = client.post("/v1/qtc/quotations", headers=auth, json={"customer_id": cat["customer"]["id"], "price_list_id": cat["pl"]["id"]})
    assert r.json()["error"]["code"] == "PRICE_LIST_NOT_APPROVED"


def test_line_discounts_and_vat_inclusive_prices(client, auth):
    cat = _catalogue(client, auth, inclusive=True)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"], "quantity": "2", "discount_percent": "10"},
                                          {"service_id": cat["edit"]["id"], "quantity": "1.5", "discount_amount": "500"}])
    a, b = q["lines"]
    # 2 x 45000 = 90000, -10% = 81000 gross incl. VAT -> net 75700.93 + VAT 5299.07
    assert (a["line_net"], a["line_vat"], a["line_total"]) == ("75700.93", "5299.07", "81000.00")
    # 1.5 x 8000 = 12000 - 500 = 11500 incl. VAT -> net 10747.66 + VAT 752.34
    assert (b["line_net"], b["line_vat"], b["line_total"]) == ("10747.66", "752.34", "11500.00")
    assert q["grand_total"] == "92500.00" and q["subtotal"] == "86448.59" and q["vat_total"] == "6051.41"
    assert q["discount_total"] == "9500.00"


def test_floats_and_bad_lines_are_refused(client, auth):
    cat = _catalogue(client, auth)
    bad = [{"service_id": cat["prod"]["id"], "quantity": 1.5}, {"service_id": cat["prod"]["id"], "discount_percent": "101"},
           {"description": "x", "unit_price": "-1"}, {"service_id": cat["prod"]["id"], "quantity": "0"}, {"description": ""}]
    for line in bad:
        r = client.post("/v1/qtc/quotations", headers=auth, json={"customer_id": cat["customer"]["id"], "lines": [line]})
        assert r.status_code == 422, line


def test_missing_price_blocks_submission_until_a_person_enters_it(client, auth):
    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["unpriced"]["id"]}, {"description": "ค่าเดินทาง", "quantity": "1"}])
    r = client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    assert r.status_code == 422 and r.json()["error"]["code"] == "PRICE_REQUIRED" and r.json()["error"]["details"]["lines"] == [1, 2]
    fixed = client.patch(f"/v1/qtc/quotations/{q['id']}", headers=auth, json={"lines": [
        {"service_id": cat["unpriced"]["id"], "unit_price": "12000"}, {"description": "ค่าเดินทาง", "unit_price": "1500.50"}]}).json()
    assert [x["price_source"] for x in fixed["lines"]] == ["manual", "manual"] and fixed["grand_total"] == "14445.54"
    assert client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth).status_code == 200
    events = client.get("/v1/qtc/audit-events", headers=auth, params={"entity_id": q["id"]}).json()["items"]
    manual = next(e for e in events if e["action"] == "quotation.updated")["detail"]["lines"]["manual_prices"]
    assert [m["unit_price"] for m in manual] == ["12000.0000", "1500.5000"]  # who typed which price is on record


def test_a_typed_price_that_differs_from_the_list_is_marked_manual(client, auth):
    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"], "unit_price": "40000"},
                                          {"service_id": cat["edit"]["id"], "unit_price": "8000"}])
    assert [x["price_source"] for x in q["lines"]] == ["manual", "price_list"]


# ---- approval and states ------------------------------------------------------------------------
def test_only_owners_and_admins_approve(client, auth, org_a):
    cat = _catalogue(client, auth)
    member = _member(client, org_a["org_id"])
    q = _create(client, member, cat, lines=[{"service_id": cat["prod"]["id"]}])
    assert client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=member).status_code == 200
    assert client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=member, json={"decision": "approved"}).status_code == 403
    key = {"X-API-Key": org_a["api_key"]}
    assert client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=key, json={"decision": "approved"}).status_code == 403
    back = client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=auth, json={"decision": "changes_requested", "comment": "ลดราคา"})
    assert back.json()["status"] == "draft" and back.json()["revision"] == 2  # the version the approver saw stays frozen
    assert client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=member).status_code == 200
    ok = client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=auth, json={"decision": "approved"}).json()
    assert ok["status"] == "approved" and ok["approved_by"].startswith("user:")
    decisions = client.get(f"/v1/qtc/quotations/{q['id']}/approvals", headers=auth).json()
    assert [(d["revision"], d["decision"], d["comment"]) for d in decisions] == [(1, "changes_requested", "ลดราคา"), (2, "approved", "")]
    assert [r["revision"] for r in client.get(f"/v1/qtc/quotations/{q['id']}/revisions", headers=auth).json()] == [1, 2]


def test_state_machine_and_acceptance_evidence(client, auth):
    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}])
    qid = q["id"]
    assert client.post(f"/v1/qtc/quotations/{qid}/send", headers=auth, json={"method": "in_person"}).status_code == 409  # not approved
    assert client.post(f"/v1/qtc/quotations/{qid}/accept", headers=auth, json={"accepted_by_name": "x", "method": "email"}).status_code == 409
    client.post(f"/v1/qtc/quotations/{qid}/submit", headers=auth)
    assert client.patch(f"/v1/qtc/quotations/{qid}", headers=auth, json={"title": "late edit"}).status_code == 409  # frozen once submitted
    client.post(f"/v1/qtc/quotations/{qid}/approve", headers=auth, json={"decision": "approved"})
    sent = client.post(f"/v1/qtc/quotations/{qid}/send", headers=auth, json={"method": "email_by_user", "note": "sent from my mailbox"}).json()
    assert sent["status"] == "sent" and sent["sent_method"] == "email_by_user"
    wrong = client.post(f"/v1/qtc/quotations/{qid}/accept", headers=auth,
                        json={"accepted_by_name": "คุณเอ", "method": "purchase_order", "revision": 2})
    assert wrong.json()["error"]["code"] == "REVISION_MISMATCH"
    body = {"accepted_by_name": "คุณเอ", "method": "purchase_order", "reference": "PO-7788", "evidence_url": "https://files.example/po-7788.pdf"}
    acc = client.post(f"/v1/qtc/quotations/{qid}/accept", headers=auth, json=body)
    assert acc.status_code == 200 and acc.json()["status"] == "accepted" and acc.json()["accepted_revision"] == 1
    assert acc.json()["acceptance"]["reference"] == "PO-7788" and acc.json()["acceptance"]["recorded_by"].startswith("user:")
    assert client.post(f"/v1/qtc/quotations/{qid}/accept", headers=auth, json=body).status_code == 200  # recorded twice: no change
    for action, payload in (("revise", {"reason": "x"}), ("cancel", {}), ("decline", {})):
        assert client.post(f"/v1/qtc/quotations/{qid}/{action}", headers=auth, json=payload).status_code == 409


def test_expired_quotations_cannot_be_accepted(client, auth, org_a):
    from app.db import session_scope
    from app.qtc.models import Quotation

    cat = _catalogue(client, auth)
    q = _approved(client, auth, cat)
    with session_scope() as session:
        session.get(Quotation, __import__("uuid").UUID(q["id"])).valid_until = date.today() - timedelta(days=1)
    assert client.get(f"/v1/qtc/quotations/{q['id']}", headers=auth).json()["status"] == "expired"
    assert client.get("/v1/qtc/quotations", headers=auth, params={"status": "expired"}).json()["total"] == 1
    assert client.get("/v1/qtc/quotations", headers=auth, params={"status": "approved"}).json()["total"] == 0
    r = client.post(f"/v1/qtc/quotations/{q['id']}/accept", headers=auth, json={"accepted_by_name": "x", "method": "email"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUOTATION_EXPIRED"
    revised = client.post(f"/v1/qtc/quotations/{q['id']}/revise", headers=auth, json={"reason": "extend validity"}).json()
    assert revised["status"] == "draft" and revised["revision"] == 2 and date.fromisoformat(revised["valid_until"]) > date.today()


def test_revisions_keep_the_approved_version_frozen(client, auth):
    cat = _catalogue(client, auth)
    q = _approved(client, auth, cat)
    qid = q["id"]
    r2 = client.post(f"/v1/qtc/quotations/{qid}/revise", headers=auth, json={"reason": "ลูกค้าขอเพิ่มงานตัดต่อ"}).json()
    assert r2["revision"] == 2 and r2["status"] == "draft" and r2["approved_by"] is None
    client.patch(f"/v1/qtc/quotations/{qid}", headers=auth, json={"lines": [{"service_id": cat["prod"]["id"]}, {"service_id": cat["edit"]["id"]}]})
    client.post(f"/v1/qtc/quotations/{qid}/submit", headers=auth)
    revs = client.get(f"/v1/qtc/quotations/{qid}/revisions", headers=auth).json()
    assert [(x["revision"], x["grand_total"]) for x in revs] == [(1, "48150.00"), (2, "56710.00")]


# ---- documents -------------------------------------------------------------------------------
def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_thai_pdf_and_docx_with_draft_watermark(client, auth, org_a):
    cat = _catalogue(client, auth)
    client.put("/v1/qtc/settings", headers=auth, json={"legal_name": "บริษัท เอ็มซีอาร์ เทคโนโลยี จำกัด", "tax_id": "0105536000313",
                                                       "address": "สุราษฎร์ธานี", "quotation_terms": "ชำระภายใน 30 วัน"})
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}], title="งานถ่ายทำวิดีโอโปรโมต")
    key = {"X-API-Key": org_a["api_key"]}

    draft = client.get(f"/v1/qtc/quotations/{q['id']}/document", headers=auth, params={"format": "docx"})
    assert draft.status_code == 200 and "DRAFT" in draft.headers["content-disposition"]
    text = _docx_text(draft.content)
    assert "ฉบับร่าง" in text and "ใบเสนอราคา" in text and q["number"] in text and "สี่หมื่นแปดพันหนึ่งร้อยห้าสิบบาทถ้วน" in text
    assert "บริษัท เอ็มซีอาร์ เทคโนโลยี จำกัด" in text and "ชำระภายใน 30 วัน" in text
    # integrations never receive an unapproved document
    assert client.get(f"/v1/qtc/quotations/{q['id']}/document", headers=key).status_code == 403

    client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)
    client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=auth, json={"decision": "approved"})
    pdf = client.get(f"/v1/qtc/quotations/{q['id']}/document", headers=key, params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    assert "DRAFT" not in pdf.headers["content-disposition"] and pdf.headers["cache-control"] == "private, no-store"
    clean = client.get(f"/v1/qtc/quotations/{q['id']}/document", headers=auth, params={"format": "docx"})
    assert "ฉบับร่าง" not in _docx_text(clean.content)
    # a later draft revision is watermarked again, the approved revision 1 stays available clean
    client.post(f"/v1/qtc/quotations/{q['id']}/revise", headers=auth, json={"reason": "x"})
    assert "ฉบับร่าง" in _docx_text(client.get(f"/v1/qtc/quotations/{q['id']}/document", headers=auth, params={"format": "docx"}).content)
    old = client.get(f"/v1/qtc/quotations/{q['id']}/document", headers=key, params={"format": "docx", "revision": 1})
    assert old.status_code == 200 and "ฉบับร่าง" not in _docx_text(old.content)


@pytest.mark.parametrize(
    ("amount", "words"),
    [("0", "ศูนย์บาทถ้วน"), ("1", "หนึ่งบาทถ้วน"), ("11", "สิบเอ็ดบาทถ้วน"), ("21.25", "ยี่สิบเอ็ดบาทยี่สิบห้าสตางค์"),
     ("101", "หนึ่งร้อยเอ็ดบาทถ้วน"), ("48150", "สี่หมื่นแปดพันหนึ่งร้อยห้าสิบบาทถ้วน"), ("1000001", "หนึ่งล้านเอ็ดบาทถ้วน"),
     ("2500000.50", "สองล้านห้าแสนบาทห้าสิบสตางค์")],
)
def test_thai_amount_in_words(amount, words):
    assert thai_baht_text(Decimal(amount)) == words


# ---- isolation, idempotency, concurrency --------------------------------------------------------
def test_tenant_isolation(client, auth, org_a, org_b):
    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}])
    other = _login(client, org_b["email"])
    assert client.get(f"/v1/qtc/quotations/{q['id']}", headers=other).status_code == 404
    assert client.patch(f"/v1/qtc/quotations/{q['id']}", headers=other, json={"title": "x"}).status_code == 404
    assert client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=other).status_code == 404
    assert client.get(f"/v1/qtc/quotations/{q['id']}/document", headers=other).status_code == 404
    assert client.get("/v1/qtc/quotations", headers=other).json()["total"] == 0
    # another organization's customer cannot be quoted
    assert client.post("/v1/qtc/quotations", headers=other, json={"customer_id": cat["customer"]["id"]}).status_code == 404


def test_creation_is_idempotent(client, auth):
    cat = _catalogue(client, auth)
    h = {**auth, "Idempotency-Key": "quote-abc-1"}
    body = {"customer_id": cat["customer"]["id"], "lines": [{"service_id": cat["prod"]["id"]}]}
    a = client.post("/v1/qtc/quotations", headers=h, json=body)
    b = client.post("/v1/qtc/quotations", headers=h, json=body)
    assert (a.status_code, b.status_code) == (201, 200) and a.json()["id"] == b.json()["id"]
    assert client.get("/v1/qtc/quotations", headers=auth).json()["total"] == 1


def test_concurrent_creation_gets_unique_numbers(client, auth):
    cat = _catalogue(client, auth)

    def create(_):
        return client.post("/v1/qtc/quotations", headers=auth, json={"customer_id": cat["customer"]["id"]}).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(create, range(10)))
    assert codes == [201] * 10
    numbers = [x["number"] for x in client.get("/v1/qtc/quotations", headers=auth, params={"limit": 50}).json()["items"]]
    assert len(set(numbers)) == 10


def test_double_approval_and_double_acceptance_cannot_both_succeed(client, auth):
    cat = _catalogue(client, auth)
    q = _create(client, auth, cat, lines=[{"service_id": cat["prod"]["id"]}])
    client.post(f"/v1/qtc/quotations/{q['id']}/submit", headers=auth)

    def approve(_):
        return client.post(f"/v1/qtc/quotations/{q['id']}/approve", headers=auth, json={"decision": "approved"}).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = sorted(pool.map(approve, range(4)))
    assert codes.count(200) == 1 and codes.count(409) == 3
    assert len(client.get(f"/v1/qtc/quotations/{q['id']}/approvals", headers=auth).json()) == 1


def test_concurrent_duplicate_acceptance_is_recorded_once(client, auth):
    cat = _catalogue(client, auth)
    q = _approved(client, auth, cat)
    body = {"accepted_by_name": "คุณเอ", "method": "email", "reference": "same email"}

    def accept(_):
        return client.post(f"/v1/qtc/quotations/{q['id']}/accept", headers=auth, json=body).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(accept, range(4)))
    assert codes == [200] * 4  # duplicates of the same acceptance are answered, not repeated
    events = client.get("/v1/qtc/audit-events", headers=auth, params={"entity_id": q["id"]}).json()["items"]
    assert [e["action"] for e in events].count("quotation.accepted") == 1
