"""Hybrid execution router: picks cloud vs local vs explicit-demo for each workload.

Inputs considered: provider availability/health, local GPU queue depth and free VRAM,
data-privacy policy, tenant authorization for cloud fallback, estimated cost vs job and
monthly budgets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AgentError
from app.models import Job, Organization, UsageEvent
from app.providers.comfyui import ComfyUIClient
from app.providers.config import (
    ComfyUIConfig,
    FireflyConfig,
    OpenAIConfig,
    resolve_comfyui,
    resolve_firefly,
    resolve_openai,
)
from app.providers.firefly import FireflyClient
from app.providers.openai_client import OpenAIClient

# Tests inject httpx.MockTransport instances here ({"openai": ..., "comfyui": ..., "firefly": ...}).
TRANSPORTS: dict[str, httpx.BaseTransport] = {}

ZERO = Decimal("0")


@dataclass
class Route:
    provider: str  # openai | comfyui | firefly | local_cpu | ffmpeg | rules | demo
    location: str  # cloud | local
    reason: str
    estimated_cost_usd: Decimal = ZERO
    demo: bool = False
    openai: OpenAIClient | None = None
    comfyui: ComfyUIClient | None = None
    firefly: FireflyClient | None = None


def make_openai(cfg: OpenAIConfig) -> OpenAIClient:
    return OpenAIClient(cfg, transport=TRANSPORTS.get("openai"))


def make_firefly(cfg: FireflyConfig) -> FireflyClient:
    return FireflyClient(cfg, transport=TRANSPORTS.get("firefly"))


def make_comfyui(cfg: ComfyUIConfig) -> ComfyUIClient:
    return ComfyUIClient(cfg, transport=TRANSPORTS.get("comfyui"))


# --- budget ------------------------------------------------------------------
def month_spend(session: Session, org_id) -> Decimal:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = session.scalar(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
            UsageEvent.organization_id == org_id, UsageEvent.created_at >= start
        )
    )
    return Decimal(str(total or 0))


def ensure_budget(session: Session, job: Job, org: Organization, estimated: Decimal) -> None:
    """Reserve the estimate of a paid call against the job and monthly budgets, atomically, or refuse
    it (BUDGET_EXCEEDED / MONTHLY_BUDGET_EXCEEDED). The reservation is settled when the call is billed
    and released when the task ends (app.services.budget)."""
    from app.services import budget

    budget.reserve(session, job, org, estimated)


# --- policy ---------------------------------------------------------------------
def external_ai_allowed(job: Job, org: Organization) -> tuple[bool, str]:
    if job.data_classification == "restricted" and not org.allow_external_ai_for_restricted:
        return False, "data is classified 'restricted' and external AI is not authorized for this organization"
    return True, ""


def _demo_or_fail(code: str, why: str) -> Route:
    if get_settings().demo_mode:
        return Route("demo", "local", f"DEMO MODE: {why}", demo=True)
    raise AgentError(code, f"{why}. Configure a provider (Settings → Providers) or enable DEMO_MODE explicitly.")


# --- routes ------------------------------------------------------------------------
def route_llm(session: Session, job: Job, org: Organization, *, required: bool = True) -> Route | None:
    """Text workloads (planning, content, document copy, scripts, QA review) → cloud LLM."""
    s = get_settings()
    allowed, why = external_ai_allowed(job, org)
    cfg = resolve_openai(session, org.id)
    if cfg and allowed:
        ensure_budget(session, job, org, s.llm_estimated_usd_per_call)
        return Route("openai", "cloud", f"cloud LLM ({cfg.model})", s.llm_estimated_usd_per_call, openai=make_openai(cfg))
    if not required:
        return None
    if cfg and not allowed:
        return _demo_or_fail("PRIVACY_POLICY_BLOCKED", f"Cloud LLM not used: {why}")
    return _demo_or_fail("PROVIDER_NOT_CONFIGURED", "No cloud LLM provider is configured")


def route_image(session: Session, job: Job, org: Organization) -> Route:
    """Images → a cloud provider the organization chose as its main image provider (Firefly, then OpenAI);
    otherwise local ComfyUI when healthy, then an authorized cloud fallback (Firefly before OpenAI).
    Cloud is never used for restricted data unless the organization allows it, and every paid call is
    checked against the budgets."""
    s = get_settings()
    allowed, why = external_ai_allowed(job, org)
    firefly_cfg = resolve_firefly(session, org.id)

    def firefly_route(note: str) -> Route:
        assert firefly_cfg is not None
        ensure_budget(session, job, org, s.firefly_usd_per_image)
        model = firefly_cfg.model_version or "default model"
        return Route("firefly", "cloud", f"{note}Adobe Firefly ({model}, {firefly_cfg.content_class})",
                     s.firefly_usd_per_image, firefly=make_firefly(firefly_cfg))

    cfg = resolve_openai(session, org.id)

    def openai_route(note: str) -> Route:
        assert cfg is not None
        ensure_budget(session, job, org, s.image_usd_per_image)
        return Route("openai", "cloud", f"{note}OpenAI images ({cfg.image_model})", s.image_usd_per_image,
                     openai=make_openai(cfg))

    # A provider the organization chose to use before ComfyUI (Settings → AI providers → "when to use").
    if allowed and firefly_cfg and firefly_cfg.priority == "first":
        return firefly_route("preferred provider: ")
    if allowed and cfg and cfg.image_priority == "first":
        return openai_route("preferred provider: ")

    local_note = "local ComfyUI is not configured"
    comfy_cfg = resolve_comfyui(session, org.id)
    if comfy_cfg:
        client = make_comfyui(comfy_cfg)
        health = client.health()
        if not health.reachable:
            local_note = f"local ComfyUI {health.detail}"
        elif health.queue_depth > s.comfyui_max_queue:
            local_note = f"local ComfyUI queue is saturated ({health.queue_depth} > {s.comfyui_max_queue})"
        else:
            return Route("comfyui", "local", f"local GPU available ({health.detail}); no API cost", comfyui=client)

    if firefly_cfg or cfg:
        preferred = [name for name, c in (("Adobe Firefly", firefly_cfg and firefly_cfg.priority == "first"),
                                          ("OpenAI images", cfg and cfg.image_priority == "first")) if c]
        if preferred and not allowed:
            local_note += f"; {' / '.join(preferred)} not used: {why}"
        if not org.allow_cloud_fallback:
            return _demo_or_fail("CLOUD_FALLBACK_NOT_AUTHORIZED", f"{local_note}; cloud fallback is disabled for this organization")
        if not allowed:
            return _demo_or_fail("PRIVACY_POLICY_BLOCKED", f"{local_note}; cloud image API not used: {why}")
        if firefly_cfg:
            return firefly_route(f"{local_note}; authorized cloud fallback: ")
        return openai_route(f"{local_note}; authorized cloud fallback: ")
    return _demo_or_fail("PROVIDER_NOT_CONFIGURED", f"{local_note} and no cloud image provider is configured")


def route_tts(session: Session, job: Job, org: Organization, chars: int) -> Route | None:
    """Voiceover → cloud TTS when configured/authorized/affordable; otherwise None (reported as a limitation)."""
    s = get_settings()
    cfg = resolve_openai(session, org.id)
    allowed, _ = external_ai_allowed(job, org)
    if not cfg or not allowed:
        return None
    estimate = s.tts_usd_per_1k_chars * Decimal(chars) / Decimal(1000)
    try:
        ensure_budget(session, job, org, estimate)
    except AgentError:
        return None
    return Route("openai", "cloud", f"cloud TTS ({cfg.tts_model})", estimate, openai=make_openai(cfg))
