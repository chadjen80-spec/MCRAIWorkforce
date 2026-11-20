"""Content Agent: captions, hashtags, CTAs, YouTube metadata and the shared campaign brief."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from app.agents import captions as cw
from app.agents.base import AgentContext, has_thai
from app.agents.captions import CaptionDraft
from app.errors import AgentError
from app.services.router import route_llm

# A caption is written whole by the model (see app.agents.captions); this name is kept for existing imports.
Caption = CaptionDraft


class CampaignBrief(BaseModel):
    headline: str
    tagline: str
    key_messages: list[str] = Field(min_length=1)


class ContentOutput(BaseModel):
    captions: list[CaptionDraft] = Field(default_factory=list)
    brief: CampaignBrief
    youtube_title: str | None = None
    youtube_description: str | None = None


# Things a caption must not assert unless the verified business context contains them.
_CLAIM = re.compile(r"(฿|\bTHB\b|บาท|\$\s?\d|\d+\s?%|อันดับ\s?1|No\.?\s?1|guarantee|รับประกัน|การันตี)", re.I)


def find_unsupported_claims(texts: list[str], business_context: str) -> list[str]:
    flags = []
    for i, text in enumerate(texts, 1):
        for m in _CLAIM.finditer(text):
            if m.group(0).lower() not in business_context.lower():
                flags.append(f"caption {i}: contains '{m.group(0)}' which is not in the verified business context")
    return flags


def _demo_output(ctx: AgentContext, count: int, language: str, youtube: bool) -> ContentOutput:
    product = ctx.brand["product"]
    if language == "th":
        captions = [
            CaptionDraft(
                text=f"[DEMO] ตัวอย่างแคปชันที่ {i} สำหรับ {product} — ข้อความนี้มาจากโหมดสาธิต ยังไม่ได้สร้างโดย AI "
                     f"กรุณาตั้งค่าผู้ให้บริการ AI เพื่อสร้างเนื้อหาจริง",
                hashtags=["#DEMO"], creative_angle="demo",
            ) for i in range(1, count + 1)
        ]
        brief = CampaignBrief(headline=f"[DEMO] {product}", tagline="[DEMO] ข้อความตัวอย่างจากโหมดสาธิต",
                              key_messages=["[DEMO] ประเด็นหลักจะถูกสร้างเมื่อเชื่อมต่อผู้ให้บริการ AI"])
    else:
        captions = [
            CaptionDraft(
                text=f"[DEMO] Sample caption {i} for {product} — produced by demo mode, not AI-generated. "
                     f"Configure an AI provider to generate real content.",
                hashtags=["#DEMO"], creative_angle="demo",
            ) for i in range(1, count + 1)
        ]
        brief = CampaignBrief(headline=f"[DEMO] {product}", tagline="[DEMO] Placeholder tagline from demo mode",
                              key_messages=["[DEMO] Key messages are generated once an AI provider is connected"])
    return ContentOutput(
        captions=captions, brief=brief,
        youtube_title=f"[DEMO] {product}" if youtube else None,
        youtube_description="[DEMO] Placeholder description from demo mode." if youtube else None,
    )


def run(ctx: AgentContext) -> dict[str, Any]:
    p = ctx.task.params
    count = int(p.get("caption_count", 0))
    cap_lang = p.get("caption_language", "th")
    youtube = bool(p.get("youtube_metadata"))

    brief = cw.build_brief(ctx)
    quality: list[list[dict[str, str]]] = [[] for _ in range(count)]
    route = ctx.use_route(route_llm(ctx.session, ctx.job, ctx.org))  # type: ignore[arg-type]
    if route.demo or route.openai is None:
        out = _demo_output(ctx, count, cap_lang, youtube)
    else:
        user = (
            f"{brief.render()}\n\n"
            + (cw.deliverables(count, brief) + "\n" if count else "Write no captions (captions: []).\n")
            + "Also write the campaign brief (`brief`: headline, tagline, 3-5 key messages) that the document and video "
            f"agents reuse, in {'Thai' if p.get('language') == 'th' else 'English'}.\n"
            + ("Also write youtube_title (max 70 chars) and youtube_description (max 800 chars).\n" if youtube
               else "Set youtube_title and youtube_description to null.\n")
            + (f"Note from the QA review of the previous attempt: {p['instruction']}\n" if p.get("instruction") else "")
        )
        out, quality = cw.generate(ctx, route.openai, user, ContentOutput, count, brief)
        if cap_lang == "th" and count and not all(has_thai(c.text) for c in out.captions):
            raise AgentError("CONTENT_WRONG_LANGUAGE", "Captions were requested in Thai but are not Thai", retryable=True)

    ctx.heartbeat()
    demo = route.demo
    meta = {"provider": route.provider, "license": "generated", "language": cap_lang}
    payload = out.model_dump()
    ctx.save_artifact("copy", "content.json", "application/json", is_demo=demo, metadata=meta,
                      data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                      text_preview=f"{out.brief.headline}\n{out.brief.tagline}\n\n" + "\n".join(f"• {m}" for m in out.brief.key_messages))
    for i, cap in enumerate(out.captions, 1):
        text = format_caption(cap)
        ctx.save_artifact("caption", f"caption-{i:02d}.txt", "text/plain; charset=utf-8", is_demo=demo,
                          metadata={**meta, "index": i, "platform": cap.platform or p.get("platform", "facebook"),
                                    "tone": cap.tone, "creative_angle": cap.creative_angle,
                                    "quality": [] if demo else quality[i - 1]},
                          data=text.encode("utf-8"), text_preview=text)
    if youtube and out.youtube_title:
        yt = f"{out.youtube_title}\n\n{out.youtube_description or ''}".strip()
        ctx.save_artifact("copy", "youtube-metadata.txt", "text/plain; charset=utf-8", is_demo=demo, metadata=meta,
                          data=yt.encode("utf-8"), text_preview=yt)

    return {
        **payload,
        "caption_count": len(out.captions),
        "caption_language": cap_lang,
        "claim_flags": [] if demo else find_unsupported_claims([c.text for c in out.captions], ctx.business_context),
        "quality_flags": [] if demo else [f"caption {i}: {x['detail']}" for i, q in enumerate(quality, 1) for x in q],
        "creative_brief": brief.model_dump(),
    }


format_caption = cw.format_caption


def rewrite_caption(
    ctx: AgentContext, current: str, others: list[str], instruction: str | None, *, action: str = "regenerate",
    tone: str | None = None, current_is_demo: bool = False,
) -> tuple[str, list[str], bool, dict[str, Any]]:
    """A full rewrite of one caption of a finished job (see app.agents.captions.rewrite_caption).

    Returns (caption file text, warnings, is_demo, metadata). A real caption is never replaced by a demo
    placeholder: without a cloud model this raises PROVIDER_NOT_CONFIGURED and the caption stays as it is.
    """
    lang = (ctx.task.params or {}).get("caption_language", "th")
    route = ctx.use_route(route_llm(ctx.session, ctx.job, ctx.org))  # type: ignore[arg-type]
    if route.demo or route.openai is None:
        if not current_is_demo:
            raise AgentError("PROVIDER_NOT_CONFIGURED",
                             "No cloud AI model is available, so the caption was kept. Demo mode never replaces AI-written captions.")
        cap = CaptionDraft(
            text=("[DEMO] แคปชันตัวอย่างที่สร้างใหม่จากโหมดสาธิต ยังไม่ได้สร้างโดย AI" if lang == "th"
                  else "[DEMO] Replacement caption from demo mode, not AI-generated."),
            hashtags=["#DEMO"], creative_angle="demo",
        )
        return format_caption(cap), [], True, {"action": action}
    stored, issues, meta = cw.rewrite_caption(ctx, route.openai, action=action,  # type: ignore[arg-type]
                                              previous=current, others=others, feedback=instruction, tone=tone)
    return stored, [x["detail"] for x in issues], False, meta
