"""Auth, settings and health endpoints."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import schemas
from app.config import AGENT_QUEUES, get_settings
from app.db import get_db
from app.deps import Principal, client_ip, get_principal, rate_limited, require_admin
from app.errors import AppError, not_found, unauthenticated
from app.models import Membership, Organization, ProviderConfig, User
from app.providers.config import FIREFLY_MODELS, resolve_comfyui, resolve_firefly, resolve_openai
from app.security import create_access_token, encrypt_secret, hash_password, verify_password
from app.services import orchestrator
from app.services.router import make_comfyui, make_firefly, make_openai
from app.services.storage import get_storage

log = logging.getLogger("mcr.api")

auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])
settings_router = APIRouter(prefix="/v1/settings", tags=["settings"])
health_router = APIRouter(prefix="/health", tags=["health"])

# Constant-time-ish login: verify against a dummy hash when the user does not exist.
_DUMMY_HASH = hash_password("not-a-real-account")


@auth_router.post("/login", response_model=schemas.LoginResponse)
def login(body: schemas.LoginRequest, request: Request, session: Session = Depends(get_db)):
    settings = get_settings()
    rate_limited(f"login:{client_ip(request)}", settings.login_rate_limit_per_minute)
    email = body.email.strip().lower()
    user = session.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not ok or not user.is_active:
        raise unauthenticated("Incorrect email or password")
    membership = session.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))
    if membership is None:
        raise unauthenticated("Account has no organization")
    org = session.get(Organization, membership.organization_id)
    assert org is not None
    token, expires = create_access_token(user.id, org.id, membership.role)
    orchestrator.audit(session, org.id, "auth.login", f"user:{user.email}", detail={"ip": client_ip(request)})
    session.commit()
    return schemas.LoginResponse(
        access_token=token, expires_in=expires,
        user=schemas.UserOut(id=user.id, email=user.email, full_name=user.full_name),
        organization=schemas.OrgBrief(id=org.id, name=org.name, slug=org.slug), role=membership.role,
    )


@auth_router.post("/change-password", status_code=204, summary="Change the signed-in user's password")
def change_password(body: schemas.PasswordChange, request: Request, principal: Principal = Depends(get_principal),
                    session: Session = Depends(get_db)):
    settings = get_settings()
    if principal.user is None:
        raise AppError(403, "FORBIDDEN", "API keys cannot change a password; sign in as a user")
    # Same brute-force limit as login: this endpoint also tests a password.
    rate_limited(f"pwchange:{principal.user.id}", settings.login_rate_limit_per_minute)
    user = session.get(User, principal.user.id)
    assert user is not None
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(400, "WRONG_PASSWORD", "The current password is incorrect")
    if body.new_password == body.current_password:
        raise AppError(422, "VALIDATION_ERROR", "The new password must be different from the current one")
    if body.new_password.strip() != body.new_password:
        raise AppError(422, "VALIDATION_ERROR", "The new password must not start or end with a space")
    user.password_hash = hash_password(body.new_password)
    orchestrator.audit(session, principal.organization.id, "auth.password_changed", principal.actor,
                       detail={"ip": client_ip(request)})
    session.commit()
    return Response(status_code=204)


@auth_router.get("/me", response_model=schemas.MeResponse)
def me(principal: Principal = Depends(get_principal)):
    u, o = principal.user, principal.organization
    return schemas.MeResponse(
        user=schemas.UserOut(id=u.id, email=u.email, full_name=u.full_name) if u else None,
        organization=schemas.OrgBrief(id=o.id, name=o.name, slug=o.slug), role=principal.role,
    )


# --- settings -------------------------------------------------------------------
def _org_out(org: Organization) -> schemas.OrganizationOut:
    return schemas.OrganizationOut(
        id=org.id, name=org.name, slug=org.slug, monthly_budget_usd=float(org.monthly_budget_usd),
        default_job_budget_usd=float(org.default_job_budget_usd), allow_cloud_fallback=org.allow_cloud_fallback,
        allow_external_ai_for_restricted=org.allow_external_ai_for_restricted, business_context=org.business_context or "",
    )


def _providers(session: Session, org: Organization, *, probe: bool) -> list[schemas.ProviderOut]:
    rows = {r.name: r for r in session.scalars(select(ProviderConfig).where(ProviderConfig.organization_id == org.id))}
    out = []
    openai = resolve_openai(session, org.id)
    enabled = rows["openai"].enabled if "openai" in rows else True
    openai_healthy: bool | None = None
    openai_detail = "disabled" if not enabled else "no API key configured"
    if openai:
        openai_detail = f"chat {openai.model} · image {openai.image_model} · tts {openai.tts_model}"
        if probe:
            openai_healthy, note = make_openai(openai).check()
            openai_detail += f" · {note}"
    openai_options = {"image_priority": (rows["openai"].config or {}).get("image_priority") or "fallback"
                      if "openai" in rows else "fallback"}
    if openai:
        openai_detail += (" · images before ComfyUI" if openai.image_priority == "first"
                          else " · images after ComfyUI (fallback)")
    out.append(schemas.ProviderOut(
        name="openai", kind="llm", location="cloud", configured=openai is not None, enabled=enabled,
        healthy=openai_healthy, detail=openai_detail, options=openai_options,
    ))
    comfy = resolve_comfyui(session, org.id)
    enabled = rows["comfyui"].enabled if "comfyui" in rows else True
    healthy, detail = None, "disabled" if not enabled else "COMFYUI_URL not configured"
    if comfy:
        detail = f"{comfy.url} · checkpoint {comfy.checkpoint}"
        if probe:
            health = make_comfyui(comfy).health()
            healthy = health.reachable
            detail += f" · {health.detail}"
    out.append(schemas.ProviderOut(name="comfyui", kind="image", location="local", configured=comfy is not None,
                                   enabled=enabled, healthy=healthy, detail=detail))
    firefly = resolve_firefly(session, org.id)
    row = rows.get("firefly")
    enabled = row.enabled if row else True
    healthy, detail = None, "disabled" if not enabled else "no Adobe Client ID / Client Secret configured"
    cfg = dict(row.config or {}) if row else {}
    options = {"priority": cfg.get("priority") or "fallback", "model": cfg.get("model") or "",
               "content_class": cfg.get("content_class") or "art"}
    if cfg.get("client_id"):
        cid = cfg["client_id"]
        options["client_id"] = f"{cid[:4]}…{cid[-4:]}" if len(cid) > 10 else "…"
    if firefly:
        order = "before ComfyUI" if firefly.priority == "first" else "after ComfyUI (fallback)"
        detail = f"model {firefly.model_version or 'Adobe default'} · {firefly.content_class} · {order}"
        if probe:
            healthy, note = make_firefly(firefly).check()
            detail += f" · {note}"
    out.append(schemas.ProviderOut(name="firefly", kind="image", location="cloud", configured=firefly is not None,
                                   enabled=enabled, healthy=healthy, detail=detail, options=options))
    return out


def _workers(session: Session | None = None) -> list[schemas.WorkerOut]:
    settings = get_settings()
    counts = dict.fromkeys(AGENT_QUEUES, 0)
    if session is not None:
        from app.services.workers import queue_status

        counts = queue_status(session)
    if not any(counts.values()) and not settings.celery_task_always_eager:
        try:
            from app.worker.celery_app import celery_app

            active = celery_app.control.inspect(timeout=1.0).active_queues() or {}
            for queues in active.values():
                for q in queues:
                    if q.get("name") in counts:
                        counts[q["name"]] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("worker inspection failed: %s", exc)
    return [schemas.WorkerOut(queue=q, online=n > 0, workers=n) for q, n in counts.items()]


@settings_router.get("", response_model=schemas.SettingsOut)
def get_settings_view(principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    org = principal.organization
    return schemas.SettingsOut(organization=_org_out(org), providers=_providers(session, org, probe=True),
                               workers=_workers(session), demo_mode_enabled=get_settings().demo_mode)


@settings_router.patch("/organization", response_model=schemas.OrganizationOut)
def patch_org(body: schemas.OrganizationPatch, principal: Principal = Depends(require_admin),
              session: Session = Depends(get_db)):
    org = session.get(Organization, principal.organization.id)
    assert org is not None
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        setattr(org, field, value)
    orchestrator.audit(session, org.id, "settings.organization_updated", principal.actor,
                       detail={"fields": sorted(changes)})
    session.commit()
    return _org_out(org)


@settings_router.put("/providers/{name}", response_model=schemas.ProviderOut)
def put_provider(name: str, body: schemas.ProviderPut, principal: Principal = Depends(require_admin),
                 session: Session = Depends(get_db)):
    if name not in ("openai", "comfyui", "firefly"):
        raise not_found("Provider")
    if name != "firefly" and (body.client_id or body.priority or body.content_class):
        raise AppError(422, "VALIDATION_ERROR", "client_id, priority and content_class apply to Adobe Firefly only")
    if name != "openai" and body.image_priority is not None:
        raise AppError(422, "VALIDATION_ERROR", "image_priority applies to OpenAI only (Firefly uses priority)")
    if name == "firefly" and body.base_url is not None:
        raise AppError(422, "VALIDATION_ERROR", "The Firefly API address is fixed; set FIREFLY_API_URL to change it")
    org = principal.organization
    row = session.scalar(select(ProviderConfig).where(ProviderConfig.organization_id == org.id, ProviderConfig.name == name))
    if row is None:
        row = ProviderConfig(organization_id=org.id, name=name, config={})
        session.add(row)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.api_key is not None and name == "firefly":
        secret = body.api_key.strip().strip("\"'")
        if "..." in secret or "…" in secret or " " in secret:
            raise AppError(422, "INVALID_API_KEY", "Paste the full Client Secret from the Adobe Developer Console "
                           "(Credentials → OAuth Server-to-Server → Retrieve client secret).")
        row.encrypted_secret = encrypt_secret(secret)
    elif body.api_key is not None:
        if name != "openai":
            raise AppError(422, "VALIDATION_ERROR", "ComfyUI does not use an API key")
        api_key = body.api_key.strip().strip("\"'")
        if api_key.lower().startswith("bearer "):
            api_key = api_key[7:].strip()
        base_url = (body.base_url or (row.config or {}).get("base_url") or get_settings().openai_base_url).rstrip("/")
        if "..." in api_key or "…" in api_key:
            raise AppError(
                422, "INVALID_API_KEY",
                "This is the shortened key shown in the provider's key list (with '...'), not the secret itself. "
                "Create a new key and copy the full value from the pop-up shown right after creating it.",
            )
        if "api.openai.com" in base_url and (not api_key.startswith("sk-") or len(api_key) < 40):
            raise AppError(
                422, "INVALID_API_KEY",
                "This is not a complete OpenAI secret key. OpenAI keys start with 'sk-' (usually 'sk-proj-') and are "
                "long. Create one at platform.openai.com → API keys and paste the whole key.",
            )
        row.encrypted_secret = encrypt_secret(api_key)
    config = dict(row.config or {})
    if body.base_url is not None:
        config["base_url"] = body.base_url.rstrip("/")
    if body.model is not None:
        if name == "firefly" and body.model not in ("", *FIREFLY_MODELS):
            raise AppError(422, "VALIDATION_ERROR", f"Firefly model must be one of {', '.join(FIREFLY_MODELS)} (or blank)")
        config["model"] = body.model
    if body.client_id is not None:
        client_id = body.client_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", client_id):
            raise AppError(422, "VALIDATION_ERROR", "The Client ID is a single word of letters and numbers (copy it "
                           "from the Developer Console project's OAuth Server-to-Server credential)")
        config["client_id"] = client_id
    if body.priority is not None:
        config["priority"] = body.priority
    if body.image_priority is not None:
        config["image_priority"] = body.image_priority
    if body.content_class is not None:
        config["content_class"] = body.content_class
    row.config = config
    # Never log or store the key in the audit trail: only which fields changed.
    orchestrator.audit(session, org.id, "settings.provider_updated", principal.actor,
                       detail={"provider": name, "fields": sorted(body.model_dump(exclude_unset=True, exclude_none=True))})
    session.commit()
    return next(p for p in _providers(session, org, probe=True) if p.name == name)


# --- health -----------------------------------------------------------------------
@health_router.get("/live")
def live():
    return {"status": "ok"}


@health_router.get("/ready")
def ready(session: Session = Depends(get_db)):
    settings = get_settings()
    checks: dict[str, str] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        if settings.celery_task_always_eager:
            checks["broker"] = "ok (eager)"
        else:
            from app.worker.celery_app import celery_app

            with celery_app.connection_for_write() as conn:
                conn.ensure_connection(max_retries=1, timeout=2)
            checks["broker"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["broker"] = f"error: {type(exc).__name__}"
    try:
        get_storage().check()
        checks["storage"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["storage"] = f"error: {type(exc).__name__}"
    ok = all(v.startswith("ok") for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)
