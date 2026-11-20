"""Operational commands.

    python -m app.cli seed                 create organization + owner from BOOTSTRAP_* env vars (idempotent)
    python -m app.cli create-api-key NAME [--scopes a,b]  print a new service API key once (default scope: jobs)
    python -m app.cli generate-secrets     print fresh JWT_SECRET / SECRETS_ENCRYPTION_KEY values
    python -m app.cli reap                 run the lease reaper once
    python -m app.cli seed-qtc-demo        add example customers, services and a DRAFT price list (Quote-to-Cash)
"""
from __future__ import annotations

import re
import secrets
import sys

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import ApiKey, Membership, Organization, Project, User
from app.security import generate_api_key, hash_password


def seed() -> int:
    s = get_settings()
    if not s.bootstrap_admin_email or not s.bootstrap_admin_password:
        print("Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD first.", file=sys.stderr)
        return 2
    if len(s.bootstrap_admin_password) < 10:
        print("BOOTSTRAP_ADMIN_PASSWORD must be at least 10 characters.", file=sys.stderr)
        return 2
    email = s.bootstrap_admin_email.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", s.bootstrap_org_name.lower()).strip("-") or "org"
    with session_scope() as session:
        org = session.scalar(select(Organization).where(Organization.slug == slug))
        if org is None:
            org = Organization(name=s.bootstrap_org_name, slug=slug, monthly_budget_usd=s.default_monthly_budget_usd,
                               default_job_budget_usd=s.default_job_budget_usd)
            session.add(org)
            session.flush()
            session.add(Project(organization_id=org.id, name="Default", brand_guidelines={}))
            print(f"created organization '{org.name}'")
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, full_name="Administrator", password_hash=hash_password(s.bootstrap_admin_password))
            session.add(user)
            session.flush()
            print(f"created user {email}")
        if session.scalar(select(Membership).where(Membership.user_id == user.id, Membership.organization_id == org.id)) is None:
            session.add(Membership(organization_id=org.id, user_id=user.id, role="owner"))
    print("seed complete")
    return 0


def create_api_key(name: str, scopes: list[str] | None = None) -> int:
    from app.deps import SCOPES

    scopes = scopes or ["jobs"]
    unknown = [x for x in scopes if x not in SCOPES]
    if unknown:
        print(f"Unknown scope(s): {', '.join(unknown)}. Known: {', '.join(SCOPES)}", file=sys.stderr)
        return 2
    with session_scope() as session:
        org = session.scalar(select(Organization).order_by(Organization.created_at))
        if org is None:
            print("No organization yet. Run: python -m app.cli seed", file=sys.stderr)
            return 2
        key, prefix, digest = generate_api_key()
        session.add(ApiKey(organization_id=org.id, name=name, key_prefix=prefix, key_hash=digest, scopes=scopes))
    print(f"Scopes: {', '.join(scopes)}")
    print(f"API key '{name}' for organization '{org.name}' (shown once, store it in your secret manager):\n{key}")
    return 0


def seed_qtc_demo() -> int:
    from app.qtc.seed import seed_demo

    with session_scope() as session:
        org = session.scalar(select(Organization).order_by(Organization.created_at))
        if org is None:
            print("No organization yet. Run: python -m app.cli seed", file=sys.stderr)
            return 2
        created = seed_demo(session, org.id)
    print(f"Example Quote-to-Cash data for '{org.name}': {created}. The example price list is a DRAFT: "
          "an owner or admin must review and approve it before quotations can use it.")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "seed":
        return seed()
    if cmd == "create-api-key":
        rest = argv[1:]
        scopes = None
        if "--scopes" in rest:
            i = rest.index("--scopes")
            scopes = [x.strip() for x in (rest[i + 1] if i + 1 < len(rest) else "").split(",") if x.strip()]
            rest = rest[:i] + rest[i + 2 :]
        return create_api_key(rest[0] if rest else "service", scopes)
    if cmd == "generate-secrets":
        print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
        print(f"SECRETS_ENCRYPTION_KEY={Fernet.generate_key().decode()}")
        return 0
    if cmd == "seed-qtc-demo":
        return seed_qtc_demo()
    if cmd == "reap":
        from app.worker.celery_app import reap

        print(reap())
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
