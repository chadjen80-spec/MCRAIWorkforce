"""Example Quote-to-Cash data for a demo or a new installation.

Every record is marked as an example. The price list is created as a DRAFT on purpose: prices are
commercial decisions, so an owner or admin must review and approve them in the app before any quotation
can use them. Running it twice changes nothing (codes are unique).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.qtc import audit, numbering
from app.qtc.models import Customer, CustomerContact, PriceList, PriceListItem, ServiceItem

DEMO_SERVICES = [
    # code, Thai name, English name, category, unit, keywords, example price (THB, excluding VAT)
    ("PROD-VIDEO", "งานถ่ายทำวิดีโอ (ตัวอย่าง)", "Video production (example)", "Production", "งาน", ["production", "โปรดักชั่น", "ถ่ายทำ", "วิดีโอ"], "45000"),
    ("EDIT-VIDEO", "ตัดต่อวิดีโอ (ตัวอย่าง)", "Video editing (example)", "Post-production", "ชิ้น", ["ตัดต่อ", "edit"], "8000"),
    ("DESIGN-POSTER", "ออกแบบโปสเตอร์ (ตัวอย่าง)", "Poster design (example)", "Design", "ชิ้น", ["โปสเตอร์", "poster"], "3500"),
    ("SOCIAL-CAPTION", "เขียนแคปชันโซเชียล (ตัวอย่าง)", "Social captions (example)", "Content", "ชุด", ["แคปชัน", "caption"], "1500"),
    ("LIVE-STREAM", "ไลฟ์สตรีมงานอีเวนต์ (ตัวอย่าง)", "Event live stream (example)", "Production", "วัน", ["ไลฟ์", "live"], "25000"),
]

DEMO_CUSTOMERS = [
    ("ABC Studio (ตัวอย่าง)", "บริษัท เอบีซี สตูดิโอ จำกัด (ตัวอย่าง)", "คุณเอ (ตัวอย่าง)"),
    ("Surat Esports Club (ตัวอย่าง)", "", "คุณบี (ตัวอย่าง)"),
]


def seed_demo(session: Session, org_id: uuid.UUID, actor: str = "cli:seed-qtc-demo") -> dict[str, int]:
    created = {"services": 0, "customers": 0, "prices": 0}
    services: dict[str, ServiceItem] = {}
    for code, name, name_en, category, unit, keywords, _ in DEMO_SERVICES:
        svc = session.scalar(select(ServiceItem).where(ServiceItem.organization_id == org_id, ServiceItem.code == code))
        if svc is None:
            svc = ServiceItem(organization_id=org_id, code=code, name=name, name_en=name_en, category=category, unit=unit, keywords=keywords)
            session.add(svc)
            session.flush()
            audit.record(session, org_id, "service", svc.id, "service.created", actor, {"code": code, "example": True})
            created["services"] += 1
        services[code] = svc

    pl = session.scalar(select(PriceList).where(PriceList.organization_id == org_id, PriceList.name == "ราคาตัวอย่าง (ต้องอนุมัติก่อนใช้)"))
    if pl is None:
        pl = PriceList(organization_id=org_id, name="ราคาตัวอย่าง (ต้องอนุมัติก่อนใช้)", vat_rate=Decimal("0.0700"), prices_include_vat=False)
        session.add(pl)
        session.flush()
        for code, *_, price in DEMO_SERVICES:
            session.add(PriceListItem(organization_id=org_id, price_list_id=pl.id, service_id=services[code].id, unit_price=Decimal(price),
                                      notes="example price: review before approving"))
            created["prices"] += 1
        audit.record(session, org_id, "price_list", pl.id, "price_list.created", actor, {"example": True, "status": "draft"})

    for name, legal, contact in DEMO_CUSTOMERS:
        if session.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.name == name)) is not None:
            continue
        code = f"C{numbering.take(session, org_id, 'customer_code'):04d}"
        c = Customer(organization_id=org_id, code=code, name=name, legal_name=legal, notes="Example customer created by seed-qtc-demo.",
                     created_by=actor)
        session.add(c)
        session.flush()
        session.add(CustomerContact(organization_id=org_id, customer_id=c.id, name=contact, is_primary=True))
        audit.record(session, org_id, "customer", c.id, "customer.created", actor, {"code": code, "example": True})
        created["customers"] += 1
    return created
