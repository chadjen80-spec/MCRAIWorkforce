"""Quote-to-Cash Phase A: money rules, customers, contacts, service catalogue, price lists, audit."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from app.qtc.money import (
    MoneyError,
    document_totals,
    line_amounts,
    q2,
    thai_tax_id_valid,
    to_decimal,
)

D = Decimal
VALID_TAX_ID = "0105536000313"  # valid check digit (format test only, not a real company)


def _tax_ok() -> str:
    assert thai_tax_id_valid(VALID_TAX_ID)
    return VALID_TAX_ID


def _member(client, org_id, email="sales@example.com"):
    from app.db import session_scope
    from app.models import Membership, User
    from app.security import hash_password
    from tests.conftest import PASSWORD

    with session_scope() as session:
        user = User(email=email, full_name="Sales Member", password_hash=hash_password(PASSWORD))
        session.add(user)
        session.flush()
        session.add(Membership(organization_id=org_id, user_id=user.id, role="member"))
    token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---- money -------------------------------------------------------------------------------
class TestMoney:
    def test_vat_added_on_top_rounds_half_up_per_line(self):
        a = line_amounts(D("3"), D("333.335"), vat_rate=D("0.07"))
        assert a.net == D("1000.01")  # 1000.005 -> 1000.01 (half up)
        assert a.vat == D("70.00")  # 70.0007 -> 70.00
        assert a.total == D("1070.01")

    def test_vat_extracted_from_inclusive_prices(self):
        a = line_amounts(D("1"), D("1070"), vat_rate=D("0.07"), prices_include_vat=True)
        assert (a.net, a.vat, a.total) == (D("1000.00"), D("70.00"), D("1070.00"))
        b = line_amounts(D("1"), D("100"), vat_rate=D("0.07"), prices_include_vat=True)
        assert b.net + b.vat == b.total == D("100.00")  # 93.46 + 6.54: always adds up to the price paid

    def test_discounts_percent_then_amount(self):
        a = line_amounts(D("2"), D("5000"), discount_percent=D("10"), discount_amount=D("500"), vat_rate=D("0.07"))
        assert a.gross_before_discount == D("10000.00")
        assert a.discount == D("1500.00")
        assert (a.net, a.vat, a.total) == (D("8500.00"), D("595.00"), D("9095.00"))

    def test_vat_exempt_and_zero_rate(self):
        assert line_amounts(D("1"), D("100"), vat_exempt=True).vat == D("0.00")
        assert line_amounts(D("1"), D("100"), vat_rate=D("0")).total == D("100.00")

    def test_document_totals_are_sums_of_rounded_lines(self):
        lines = [line_amounts(D("1"), D("0.105"), vat_rate=D("0.07")) for _ in range(3)]
        t = document_totals(lines)
        assert t.subtotal == D("0.33")  # 3 x 0.11, not round(0.315)
        assert t.total == sum((x.total for x in lines), D("0"))

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"quantity": D("0"), "unit_price": D("1")}, "quantity"),
            ({"quantity": D("1"), "unit_price": D("-1")}, "negative"),
            ({"quantity": D("1"), "unit_price": D("100"), "discount_percent": D("101")}, "between 0 and 100"),
            ({"quantity": D("1"), "unit_price": D("100"), "discount_amount": D("100.01")}, "larger than"),
            ({"quantity": D("1"), "unit_price": D("100"), "vat_rate": D("7")}, "fraction"),
        ],
    )
    def test_rejects_impossible_lines(self, kwargs, message):
        with pytest.raises(MoneyError, match=message):
            line_amounts(**kwargs)

    def test_floats_are_refused_strings_and_ints_accepted(self):
        with pytest.raises(MoneyError, match="floating-point"):
            to_decimal(0.1)
        assert to_decimal("0.1") + to_decimal("0.2") == D("0.3")
        assert to_decimal(5) == D("5")
        assert q2(D("2.675")) == D("2.68")

    def test_thai_tax_id_check_digit(self):
        assert thai_tax_id_valid(_tax_ok())
        assert not thai_tax_id_valid("0105536000314")
        assert not thai_tax_id_valid("12345")


# ---- customers -------------------------------------------------------------------------
def test_customer_crm_flow(client, auth):
    r = client.post("/v1/qtc/customers", headers=auth, json={
        "name": "ABC Studio", "legal_name": "บริษัท เอบีซี สตูดิโอ จำกัด", "tax_id": "0-1055-36000-31-3",
        "contacts": [{"name": "Khun Ann", "email": "ann@abc.example", "is_primary": True, "consent_email": True},
                     {"name": "Khun Ben", "is_primary": True}],
    })
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["code"] == "C0001" and c["tax_id"] == VALID_TAX_ID and c["branch"] == "00000"
    assert [x["is_primary"] for x in c["contacts"]] == [True, False]  # only one primary contact
    assert client.post("/v1/qtc/customers", headers=auth, json={"name": "XYZ"}).json()["code"] == "C0002"

    p = client.patch(f"/v1/qtc/customers/{c['id']}", headers=auth, json={"phone": "02-000-0000", "payment_terms_days": 45})
    assert p.status_code == 200 and p.json()["payment_terms_days"] == 45
    assert client.patch(f"/v1/qtc/customers/{c['id']}", headers=auth, json={"code": "C0002"}).status_code == 409

    contact_id = c["contacts"][1]["id"]
    assert client.patch(f"/v1/qtc/customers/{c['id']}/contacts/{contact_id}", headers=auth, json={"is_primary": True}).status_code == 200
    contacts = client.get(f"/v1/qtc/customers/{c['id']}", headers=auth).json()["contacts"]
    assert sum(x["is_primary"] for x in contacts) == 1 and contacts[0]["id"] == contact_id

    found = client.get("/v1/qtc/customers", headers=auth, params={"q": "เอบีซี"}).json()
    assert found["total"] == 1 and found["items"][0]["id"] == c["id"]
    client.patch(f"/v1/qtc/customers/{c['id']}", headers=auth, json={"status": "archived"})
    assert client.get("/v1/qtc/customers", headers=auth).json()["total"] == 1
    assert client.get("/v1/qtc/customers", headers=auth, params={"status": "all"}).json()["total"] == 2

    events = client.get("/v1/qtc/audit-events", headers=auth, params={"entity_id": c["id"]}).json()["items"]
    actions = [e["action"] for e in events]
    assert "customer.created" in actions and "customer.updated" in actions and "contact.updated" in actions
    upd = next(e for e in events if e["action"] == "customer.updated" and "payment_terms_days" in e["detail"])
    assert upd["detail"]["payment_terms_days"] == {"from": 30, "to": 45}


def test_customer_validation(client, auth):
    bad = client.post("/v1/qtc/customers", headers=auth, json={"name": "X", "tax_id": "0105536000314"})
    assert bad.status_code == 422
    assert client.post("/v1/qtc/customers", headers=auth, json={"name": "X", "branch": "1"}).status_code == 422
    assert client.post("/v1/qtc/customers", headers=auth, json={"name": "X", "contacts": [{"name": "A", "email": "nope"}]}).status_code == 422
    assert client.post("/v1/qtc/customers", headers=auth, json={"name": "X", "unknown": 1}).status_code == 422
    assert client.post("/v1/qtc/customers", headers=auth, json={"name": ""}).status_code == 422


def test_customer_creation_is_idempotent(client, auth):
    h = {**auth, "Idempotency-Key": "inquiry-42"}
    first = client.post("/v1/qtc/customers", headers=h, json={"name": "Repeat Co"})
    again = client.post("/v1/qtc/customers", headers=h, json={"name": "Repeat Co"})
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert client.get("/v1/qtc/customers", headers=auth).json()["total"] == 1


def test_generated_codes_skip_codes_typed_by_hand(client, auth):
    assert client.post("/v1/qtc/customers", headers=auth, json={"name": "A"}).json()["code"] == "C0001"
    assert client.post("/v1/qtc/customers", headers=auth, json={"name": "B", "code": "C0002"}).json()["code"] == "C0002"
    assert client.post("/v1/qtc/customers", headers=auth, json={"name": "C"}).json()["code"] == "C0003"


def test_concurrent_customer_creation_gets_unique_codes(client, auth):
    def create(i: int) -> int:
        return client.post("/v1/qtc/customers", headers=auth, json={"name": f"Parallel {i}"}).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(create, range(12)))
    assert all(c == 201 for c in codes), codes
    items = client.get("/v1/qtc/customers", headers=auth, params={"limit": 100}).json()["items"]
    assert len({x["code"] for x in items}) == 12


# ---- services and price lists ------------------------------------------------------------
def _service(client, auth, code="PROD-1", name="Video production", **extra):
    r = client.post("/v1/qtc/services", headers=auth, json={"code": code, "name": name, "unit": "งาน", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_price_list_approval_and_freezing(client, auth, org_a):
    svc = _service(client, auth, keywords=["production", "โปรดักชั่น"])
    assert client.post("/v1/qtc/services", headers=auth, json={"code": "PROD-1", "name": "dup"}).status_code == 409
    pl = client.post("/v1/qtc/price-lists", headers=auth, json={"name": "Rate card 2026", "vat_rate": "0.07"}).json()
    assert pl["status"] == "draft" and pl["vat_rate"] == "0.0700"

    # nothing to approve yet
    assert client.post(f"/v1/qtc/price-lists/{pl['id']}/activate", headers=auth).status_code == 422
    r = client.put(f"/v1/qtc/price-lists/{pl['id']}/items/{svc['id']}", headers=auth, json={"unit_price": "45000.00"})
    assert r.status_code == 200 and r.json()["items"][0]["unit_price"] == "45000.0000"
    assert client.put(f"/v1/qtc/price-lists/{pl['id']}/items/{svc['id']}", headers=auth, json={"unit_price": 45000.5}).status_code == 422

    member = _member(client, org_a["org_id"])
    assert client.post(f"/v1/qtc/price-lists/{pl['id']}/activate", headers=member).status_code == 403
    active = client.post(f"/v1/qtc/price-lists/{pl['id']}/activate", headers=auth).json()
    assert active["status"] == "active" and active["is_default"] and active["approved_by"].startswith("user:")

    # approved prices are frozen
    locked = client.put(f"/v1/qtc/price-lists/{pl['id']}/items/{svc['id']}", headers=auth, json={"unit_price": "1"})
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "PRICE_LIST_LOCKED"
    assert client.get(f"/v1/qtc/services/{svc['id']}", headers=auth).json()["default_price"] == "45000.0000"

    # the way to change a price: copy, edit the draft, approve it (it becomes the only default)
    draft = client.post(f"/v1/qtc/price-lists/{pl['id']}/copy", headers=auth, params={"name": "Rate card 2027"}).json()
    assert draft["status"] == "draft" and draft["items"][0]["unit_price"] == "45000.0000"
    client.put(f"/v1/qtc/price-lists/{draft['id']}/items/{svc['id']}", headers=auth, json={"unit_price": "48000"})
    client.post(f"/v1/qtc/price-lists/{draft['id']}/activate", headers=auth)
    lists = client.get("/v1/qtc/price-lists", headers=auth).json()["items"]
    assert [x["is_default"] for x in lists].count(True) == 1
    assert client.get(f"/v1/qtc/services/{svc['id']}", headers=auth).json()["default_price"] == "48000.0000"


def test_one_default_price_list_is_enforced_by_the_database(client, auth, org_a):
    from sqlalchemy.exc import IntegrityError

    from app.db import session_scope
    from app.qtc.models import PriceList

    with pytest.raises(IntegrityError), session_scope() as session:
        session.add(PriceList(organization_id=org_a["org_id"], name="A", is_default=True))
        session.add(PriceList(organization_id=org_a["org_id"], name="B", is_default=True))
        session.flush()


def test_members_edit_records_but_service_keys_only_read(client, auth, org_a):
    member = _member(client, org_a["org_id"])
    assert client.post("/v1/qtc/customers", headers=member, json={"name": "By member"}).status_code == 201
    key = {"X-API-Key": org_a["api_key"]}
    assert client.get("/v1/qtc/customers", headers=key).status_code == 200
    denied = client.post("/v1/qtc/customers", headers=key, json={"name": "By n8n"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "FORBIDDEN"
    assert client.post("/v1/qtc/services", headers=key, json={"code": "X", "name": "X"}).status_code == 403
    assert client.get("/v1/qtc/audit-events", headers=member).status_code == 403  # audit trail: owners and admins


def test_tenant_isolation(client, auth, org_a, org_b):
    from tests.conftest import PASSWORD

    other = {"Authorization": "Bearer " + client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()["access_token"]}
    cust = client.post("/v1/qtc/customers", headers=auth, json={"name": "Secret client"}).json()
    svc = _service(client, auth)
    pl = client.post("/v1/qtc/price-lists", headers=auth, json={"name": "Private rates"}).json()

    assert client.get("/v1/qtc/customers", headers=other).json()["total"] == 0
    assert client.get(f"/v1/qtc/customers/{cust['id']}", headers=other).status_code == 404
    assert client.patch(f"/v1/qtc/customers/{cust['id']}", headers=other, json={"name": "hijack"}).status_code == 404
    assert client.post(f"/v1/qtc/customers/{cust['id']}/contacts", headers=other, json={"name": "x"}).status_code == 404
    assert client.get(f"/v1/qtc/services/{svc['id']}", headers=other).status_code == 404
    assert client.get(f"/v1/qtc/price-lists/{pl['id']}", headers=other).status_code == 404
    # another organization's service cannot be priced on my list either
    mine = client.post("/v1/qtc/price-lists", headers=other, json={"name": "Theirs"}).json()
    assert client.put(f"/v1/qtc/price-lists/{mine['id']}/items/{svc['id']}", headers=other, json={"unit_price": "1"}).status_code == 404
    assert client.get("/v1/qtc/audit-events", headers=other).json()["total"] == 1  # only their own price list event
    # the same code may exist in two organizations
    assert client.post("/v1/qtc/customers", headers=other, json={"name": "Theirs", "code": "C0001"}).status_code == 201


def test_pagination(client, auth):
    for i in range(5):
        client.post("/v1/qtc/customers", headers=auth, json={"name": f"Customer {i}"})
    page = client.get("/v1/qtc/customers", headers=auth, params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == 2 and len(page["items"]) == 2
    assert client.get("/v1/qtc/customers", headers=auth, params={"limit": 1000}).status_code == 422


def test_example_seed_data_is_marked_and_never_approves_prices(client, auth, org_a):
    from app.db import session_scope
    from app.qtc.seed import seed_demo

    with session_scope() as session:
        first = seed_demo(session, org_a["org_id"])
    with session_scope() as session:
        again = seed_demo(session, org_a["org_id"])
    assert first == {"services": 5, "customers": 2, "prices": 5}
    assert again == {"services": 0, "customers": 0, "prices": 0}  # running it twice changes nothing
    lists = client.get("/v1/qtc/price-lists", headers=auth).json()["items"]
    assert [x["status"] for x in lists] == ["draft"]  # a person must approve example prices
    names = [c["name"] for c in client.get("/v1/qtc/customers", headers=auth).json()["items"]]
    assert all("ตัวอย่าง" in n for n in names)
    assert all(s["default_price"] is None for s in client.get("/v1/qtc/services", headers=auth).json()["items"])
