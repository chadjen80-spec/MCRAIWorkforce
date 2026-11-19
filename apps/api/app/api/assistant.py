"""Voice for the dashboard assistant: short text → speech through the configured cloud TTS provider."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import Principal, get_principal, rate_limited
from app.errors import AgentError, AppError
from app.models import UsageEvent
from app.providers.config import resolve_openai
from app.services.router import make_openai, month_spend

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])

# How the assistant should sound. Only sent to models that accept style instructions.
VOICE_STYLE = {
    "th": "พูดภาษาไทยด้วยน้ำเสียงผู้หญิงวัยรุ่นที่สดใส น่ารัก เป็นมิตร สุภาพ และชัดถ้อยชัดคำ",
    "en": "Speak as a bright, cute, friendly young woman. Warm, polite and clear.",
}


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=600)
    language: Literal["th", "en"] = "th"


@router.post(
    "/speech",
    summary="Synthesize one assistant sentence (MP3)",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}}}, 409: {"description": "No speech provider configured"}},
)
def speech(body: SpeechRequest, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    settings = get_settings()
    org = principal.organization
    rate_limited(f"assistant-speech:{org.id}", settings.assistant_speech_per_minute)
    cfg = resolve_openai(session, org.id)
    if cfg is None:
        # The dashboard falls back to the browser's built-in voice when it sees this.
        raise AppError(409, "PROVIDER_NOT_CONFIGURED", "No cloud speech provider is configured")
    text = body.text.strip()
    cost = settings.tts_usd_per_1k_chars * Decimal(len(text)) / Decimal(1000)
    if month_spend(session, org.id) + cost > org.monthly_budget_usd:
        raise AppError(402, "BUDGET_EXCEEDED", "The organization's monthly budget is exhausted")
    try:
        audio = make_openai(cfg).synthesize_speech(
            text, voice=settings.assistant_tts_voice, instructions=VOICE_STYLE[body.language]
        )
    except AgentError as exc:
        raise AppError(502, exc.code, f"Speech provider error: {exc.message[:300]}") from exc
    session.add(UsageEvent(organization_id=org.id, agent="assistant", provider="openai", operation="tts",
                           units={"characters": len(text)}, cost_usd=cost))
    session.commit()
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
