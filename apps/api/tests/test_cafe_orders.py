"""MCR Smart Café food & drink orders: menu, server-side pricing, idempotent orders, staff workflow, counter payment, Mira."""
from __future__ import annotations

import pytest

from app.cafe.models import CafeOrder, CafeOrderLine
from app.db import session_scope
from app.models import Membership, User
from app.security import hash_password
from tests.conftest import PASSWORD


@pytest.fixture
def cafe(fresh_state):
    fresh_state.cafe_enabled = True
    return fresh_state


@pytest.fixture
def shop(cafe, client, auth):
    """Seat 12 and a small menu: noodles 45, coke 20 (sale, was 25), sold-out toast."""
    seat = client.post("/v1/cafe/seats", json={"label": "12"}, headers=auth).json()
    food = client.post("/v1/cafe/menu/categories", json={"name": "อาหาร", "sort": 1}, headers=auth).json()
    drinks = client.post("/v1/cafe/menu/categories", json={"name": "เครื่องดื่ม", "sort": 2}, headers=auth).json()

    def item(body):
        r = client.post("/v1/cafe/menu/items", json=body, headers=auth)
        assert r.status_code == 201, r.text
        return r.json()

    return {
        "seat": seat,
        "food": food,
        "drinks": drinks,
        "noodles": item({"category_id": food["id"], "name": "มาม่าต้มยำ", "aliases": ["มาม่า"], "price": "45.00", "is_new": True}),
        "coke": item({"category_id": drinks["id"], "name": "โค้ก", "aliases": ["coke"], "price": "20", "list_price": "25"}),
        "toast": item({"category_id": food["id"], "name": "ขนมปังปิ้ง", "price": "30", "available": False}),
    }


def _order(client, auth, shop, lines, key=None, note=""):
    headers = {**auth, **({"Idempotency-Key": key} if key else {})}
    return client.post(f"/v1/cafe/seats/{shop['seat']['id']}/orders", json={"lines": lines, "note": note}, headers=headers)


def test_orders_module_respects_the_flag(cafe, client, auth):
    cafe.cafe_modules = "devices,sessions"
    r = client.get("/v1/cafe/menu", headers=auth)
    assert r.status_code == 404 and r.json()["error"]["code"] == "FEATURE_DISABLED"


def test_menu_prices_are_exact_and_validated(shop, client, auth):
    menu = client.get("/v1/cafe/menu", headers=auth).json()
    assert [c["name"] for c in menu["categories"]] == ["อาหาร", "เครื่องดื่ม"]
    coke = next(i for i in menu["items"] if i["name"] == "โค้ก")
    assert coke["price"] == "20.00" and coke["list_price"] == "25.00"
    bad = [
        {"price": 45.5},  # floats are refused
        {"price": "-1"},
        {"price": "10.005"},
        {"price": "20", "list_price": "15"},  # a "was" price must be higher
    ]
    for extra in bad:
        r = client.post("/v1/cafe/menu/items", json={"category_id": shop["food"]["id"], "name": "x", **extra}, headers=auth)
        assert r.status_code == 422, (extra, r.text)


def test_order_is_priced_by_the_server_in_one_transaction(shop, client, auth):
    r = _order(client, auth, shop, [{"item_id": shop["noodles"]["id"], "quantity": 2}, {"item_id": shop["coke"]["id"], "quantity": 3},
                                    {"item_id": shop["coke"]["id"], "quantity": 1}], key="k1", note="ไม่เผ็ด")
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["total"] == "170.00" and o["number"] == 1 and o["status"] == "placed" and o["payment_status"] == "unpaid"
    assert o["payment_method"] == "counter" and o["note"] == "ไม่เผ็ด" and o["seat_label"] == "12"
    assert sorted((x["name"], x["quantity"], x["line_total"]) for x in o["lines"]) == [("มาม่าต้มยำ", 2, "90.00"), ("โค้ก", 4, "80.00")]
    # the client can never send a price
    assert _order(client, auth, shop, [{"item_id": shop["coke"]["id"], "quantity": 1, "price": "0"}]).status_code == 422
    # retry with the same key: the same order, not a second one
    again = _order(client, auth, shop, [{"item_id": shop["coke"]["id"], "quantity": 9}], key="k1").json()
    assert again["id"] == o["id"] and again["total"] == "170.00"
    with session_scope() as s:
        assert s.query(CafeOrder).count() == 1 and s.query(CafeOrderLine).count() == 2
    # a later price change does not touch the placed order
    client.patch(f"/v1/cafe/menu/items/{shop['coke']['id']}", json={"price": "22", "clear_list_price": True}, headers=auth)
    assert client.get("/v1/cafe/orders", headers=auth).json()["items"][0]["total"] == "170.00"
    assert _order(client, auth, shop, [{"item_id": shop["coke"]["id"], "quantity": 1}]).json()["total"] == "22.00"


def test_unavailable_unknown_and_oversized_orders_are_refused(shop, client, auth):
    assert _order(client, auth, shop, [{"item_id": shop["toast"]["id"], "quantity": 1}]).json()["error"]["code"] == "ITEM_UNAVAILABLE"
    unknown = _order(client, auth, shop, [{"item_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}])
    assert unknown.json()["error"]["code"] == "ITEM_NOT_FOUND"
    assert _order(client, auth, shop, [{"item_id": shop["coke"]["id"], "quantity": 21}]).status_code == 422
    assert _order(client, auth, shop, [{"item_id": shop["coke"]["id"], "quantity": 15}, {"item_id": shop["coke"]["id"], "quantity": 15}]).json()[
        "error"]["code"] == "QUANTITY_TOO_HIGH"
    client.patch(f"/v1/cafe/menu/categories/{shop['drinks']['id']}", json={"active": False}, headers=auth)
    assert _order(client, auth, shop, [{"item_id": shop["coke"]["id"], "quantity": 1}]).json()["error"]["code"] == "ITEM_UNAVAILABLE"
    with session_scope() as s:
        assert s.query(CafeOrder).count() == 0


def test_staff_workflow_and_counter_payment(shop, client, auth):
    o = _order(client, auth, shop, [{"item_id": shop["noodles"]["id"], "quantity": 1}]).json()

    def status(new):
        return client.post(f"/v1/cafe/orders/{o['id']}/status", json={"status": new}, headers=auth)

    assert status("delivered").json()["error"]["code"] == "BAD_TRANSITION"  # must be prepared first
    assert status("preparing").json()["status"] == "preparing"
    paid = client.post(f"/v1/cafe/orders/{o['id']}/paid", headers=auth).json()
    assert paid["payment_status"] == "paid" and paid["paid_at"]
    assert status("cancelled").json()["error"]["code"] == "ORDER_PAID"
    assert status("delivered").json()["status"] == "delivered"
    assert client.get("/v1/cafe/orders", headers=auth).json()["items"] == []  # nothing open
    assert len(client.get("/v1/cafe/orders?status=all", headers=auth).json()["items"]) == 1
    other = _order(client, auth, shop, [{"item_id": shop["coke"]["id"], "quantity": 1}]).json()
    client.post(f"/v1/cafe/orders/{other['id']}/status", json={"status": "cancelled"}, headers=auth)
    assert client.post(f"/v1/cafe/orders/{other['id']}/paid", headers=auth).json()["error"]["code"] == "ORDER_CANCELLED"
    actions = [e["action"] for e in client.get("/v1/cafe/audit?limit=200", headers=auth).json()["items"]]
    for a in ("order.placed", "order.preparing", "order.paid", "order.delivered", "order.cancelled", "menu_item.created"):
        assert a in actions


def test_roles_and_tenants(shop, client, auth, org_a, org_b):
    with session_scope() as s:
        u = User(email="staff-o@example.com", full_name="Staff", password_hash=hash_password(PASSWORD))
        s.add(u)
        s.flush()
        s.add(Membership(organization_id=org_a["org_id"], user_id=u.id, role="member"))
    login = client.post("/v1/auth/login", json={"email": "staff-o@example.com", "password": PASSWORD}).json()
    staff = {"Authorization": f"Bearer {login['access_token']}"}
    assert client.patch(f"/v1/cafe/menu/items/{shop['coke']['id']}", json={"price": "1"}, headers=staff).status_code == 403  # staff can't set prices
    assert _order(client, staff, shop, [{"item_id": shop["coke"]["id"], "quantity": 1}]).status_code == 201  # but can take orders
    tok_b = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()["access_token"]
    auth_b = {"Authorization": f"Bearer {tok_b}"}
    assert client.get("/v1/cafe/menu", headers=auth_b).json()["items"] == []
    assert client.get("/v1/cafe/orders?status=all", headers=auth_b).json()["items"] == []
    assert _order(client, auth_b, shop, [{"item_id": shop["coke"]["id"], "quantity": 1}]).status_code == 404  # seat of another café


def test_mira_orders_from_the_menu_with_real_prices(shop, client, auth):
    ask = client.post("/v1/cafe/assistant", json={"text": "สั่งมาม่า 2 ที่ กับโค้กขวดนึง ที่เครื่อง 12"}, headers=auth).json()
    assert ask["intent"] == "order_food" and ask["tool_call"]["source"] == "rules"
    assert ask["data"]["total"] == "110.00"
    assert ask["reply"] == "สั่ง มาม่าต้มยำ x2 90 บาท, โค้ก x1 20 บาท รวม 110 บาท ส่งที่ที่นั่ง 12 ชำระที่เคาน์เตอร์ ใช่ไหมคะ"
    with session_scope() as s:
        assert s.query(CafeOrder).count() == 0  # nothing before a yes
    done = client.post("/v1/cafe/assistant/confirm", json={**ask["confirm"], "idempotency_key": "m1"}, headers=auth).json()
    assert done["reply"] == "สั่งแล้วค่ะ ออเดอร์ #1 รวม 110 บาท ชำระที่เคาน์เตอร์ได้เลยค่ะ"
    again = client.post("/v1/cafe/assistant/confirm", json={**ask["confirm"], "idempotency_key": "m1"}, headers=auth).json()
    assert again["data"]["order_id"] == done["data"]["order_id"]
    order = client.get("/v1/cafe/orders", headers=auth).json()["items"][0]
    assert order["via"] == "mira" and order["total"] == "110.00"


def test_mira_never_orders_at_a_price_she_did_not_quote(shop, client, auth):
    ask = client.post("/v1/cafe/assistant", json={"text": "ขอโค้ก 2 ขวด เครื่อง 12"}, headers=auth).json()
    assert ask["data"]["total"] == "40.00"
    client.patch(f"/v1/cafe/menu/items/{shop['coke']['id']}", json={"price": "21", "list_price": "25"}, headers=auth)
    done = client.post("/v1/cafe/assistant/confirm", json={**ask["confirm"], "idempotency_key": "m2"}, headers=auth).json()
    assert done["data"]["error"] == "PRICE_CHANGED" and "ยังไม่ได้สั่ง" in done["reply"]
    with session_scope() as s:
        assert s.query(CafeOrder).count() == 0


def test_mira_is_honest_about_the_menu(shop, client, auth):
    sold_out = client.post("/v1/cafe/assistant", json={"text": "ขอขนมปังปิ้งที่เครื่อง 12"}, headers=auth).json()
    assert sold_out["confirm"] is None and sold_out["data"]["error"] == "ITEM_UNAVAILABLE" and "หมด" in sold_out["reply"]
    vague = client.post("/v1/cafe/assistant", json={"text": "สั่งอาหารที่เครื่อง 12"}, headers=auth).json()
    assert vague["intent"] == "order_food" and vague["confirm"] is None and vague["data"]["needs"] == "items"
    # game launches and other flows are unchanged
    assert client.post("/v1/cafe/assistant", json={"text": "เครื่อง 12 เหลือเวลาเท่าไหร่"}, headers=auth).json()["intent"] == "get_remaining_time"
    assert client.post("/v1/cafe/assistant", json={"text": "ทำโปสเตอร์โปรโมชันมาม่า"}, headers=auth).json()["intent"] is None
