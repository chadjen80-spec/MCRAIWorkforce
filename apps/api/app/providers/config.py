"""Resolve provider configuration: per-organization DB config overrides environment defaults."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ProviderConfig
from app.security import decrypt_secret


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    base_url: str
    model: str
    image_model: str
    tts_model: str
    tts_voice: str
    image_priority: str = "fallback"  # images: "first" (before ComfyUI) | "fallback" (after ComfyUI)


FIREFLY_MODELS = ("image3", "image4_standard", "image4_ultra")


@dataclass(frozen=True)
class FireflyConfig:
    client_id: str
    client_secret: str
    model_version: str  # "" = Adobe's default
    content_class: str  # "art" | "photo"
    priority: str  # "first" (before ComfyUI) | "fallback" (after ComfyUI)
    api_url: str
    ims_url: str


@dataclass(frozen=True)
class ComfyUIConfig:
    url: str
    checkpoint: str


def _row(session: Session, org_id: uuid.UUID, name: str) -> ProviderConfig | None:
    return session.scalar(
        select(ProviderConfig).where(ProviderConfig.organization_id == org_id, ProviderConfig.name == name)
    )


def resolve_openai(session: Session, org_id: uuid.UUID) -> OpenAIConfig | None:
    s = get_settings()
    row = _row(session, org_id, "openai")
    if row is not None and not row.enabled:
        return None
    cfg = row.config if row else {}
    key = (decrypt_secret(row.encrypted_secret) if row and row.encrypted_secret else None) or s.openai_api_key
    if not key:
        return None
    return OpenAIConfig(
        api_key=key,
        base_url=(cfg.get("base_url") or s.openai_base_url).rstrip("/"),
        model=cfg.get("model") or s.openai_model,
        image_model=cfg.get("image_model") or s.openai_image_model,
        tts_model=cfg.get("tts_model") or s.openai_tts_model,
        tts_voice=cfg.get("tts_voice") or s.openai_tts_voice,
        image_priority=cfg.get("image_priority") or "fallback",
    )


def resolve_comfyui(session: Session, org_id: uuid.UUID) -> ComfyUIConfig | None:
    s = get_settings()
    row = _row(session, org_id, "comfyui")
    if row is not None and not row.enabled:
        return None
    cfg = row.config if row else {}
    url = cfg.get("base_url") or s.comfyui_url
    if not url:
        return None
    return ComfyUIConfig(url=url.rstrip("/"), checkpoint=cfg.get("model") or s.comfyui_checkpoint)


def resolve_firefly(session: Session, org_id: uuid.UUID) -> FireflyConfig | None:
    s = get_settings()
    row = _row(session, org_id, "firefly")
    if row is not None and not row.enabled:
        return None
    cfg = row.config if row else {}
    client_id = cfg.get("client_id") or s.firefly_client_id
    secret = (decrypt_secret(row.encrypted_secret) if row and row.encrypted_secret else None) or s.firefly_client_secret
    if not client_id or not secret:
        return None
    model = cfg.get("model", s.firefly_model_version) or ""
    return FireflyConfig(
        client_id=client_id, client_secret=secret, model_version=model if model in FIREFLY_MODELS else "",
        content_class=cfg.get("content_class") or "art", priority=cfg.get("priority") or "fallback",
        api_url=s.firefly_api_url.rstrip("/"), ims_url=s.firefly_ims_url,
    )
