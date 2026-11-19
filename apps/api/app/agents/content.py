"""Content Agent: captions, hashtags, CTAs, YouTube metadata and the shared campaign brief."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.base import AgentContext, has_thai
from app.errors import AgentError
from app.services.router import route_llm


class Caption(BaseModel):
    text: str = Field(min_length=10)
    hashtags: list[str] = Field(default_factory=list)
    call_to_action: str = ""


class CampaignBrief(BaseModel):
    headline: str
    tagline: str
    key_messages: list[str] = Field(min_length=1)


class ContentOutput(BaseModel):
    captions: list[Caption] = Field(default_factory=list)
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
            Caption(
                text=f"[DEMO] ตัวอย่างแคปชันที่ {i} สำหรับ {product} — ข้อความนี้มาจากโหมดสาธิต ยังไม่ได้สร้างโดย AI "
                     f"กรุณาตั้งค่าผู้ให้บริการ AI เพื่อสร้างเนื้อหาจริง",
                hashtags=["#DEMO"], call_to_action="[DEMO]",
            ) for i in range(1, count + 1)
        ]
        brief = CampaignBrief(headline=f"[DEMO] {product}", tagline="[DEMO] ข้อความตัวอย่างจากโหมดสาธิต",
                              key_messages=["[DEMO] ประเด็นหลักจะถูกสร้างเมื่อเชื่อมต่อผู้ให้บริการ AI"])
    else:
        captions = [
            Caption(
                text=f"[DEMO] Sample caption {i} for {product} — produced by demo mode, not AI-generated. "
                     f"Configure an AI provider to generate real content.",
                hashtags=["#DEMO"], call_to_action="[DEMO]",
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

    route = ctx.use_route(route_llm(ctx.session, ctx.job, ctx.org))  # type: ignore[arg-type]
    if route.demo or route.openai is None:
        out = _demo_output(ctx, count, cap_lang, youtube)
    else:
        user = (
            f"{prompts.context_block(ctx.business_context, ctx.brand)}\n\n"
            f"Campaign command from the business owner:\n\"\"\"\n{ctx.job.command}\n\"\"\"\n\n"
            f"Write exactly {count} captions for {p.get('platform', 'facebook')} in "
            f"{'Thai' if cap_lang == 'th' else 'English'}.\n"
            f"Campaign brief language: {'Thai' if p.get('language') == 'th' else 'English'}.\n"
            + ("Also write youtube_title (max 70 chars) and youtube_description (max 800 chars).\n" if youtube
               else "Set youtube_title and youtube_description to null.\n")
        )
        out, usage = route.openai.complete_json(prompts.CONTENT, user, ContentOutput)
        ctx.record_usage("openai", "chat", {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                         usage.cost_usd)
        if len(out.captions) != count:
            raise AgentError("CONTENT_COUNT_MISMATCH",
                             f"Requested {count} captions but the model returned {len(out.captions)}", retryable=True)
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
                          metadata={**meta, "index": i, "platform": p.get("platform", "facebook")},
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
    }


def format_caption(cap: Caption) -> str:
    """Caption text, then its call to action, then hashtags: the layout of the stored caption files."""
    text = cap.text.strip()
    if cap.call_to_action and cap.call_to_action not in text:
        text += "\n\n" + cap.call_to_action
    if cap.hashtags:
        text += "\n\n" + " ".join(h if h.startswith("#") else f"#{h}" for h in cap.hashtags)
    return text


class CaptionRewrite(BaseModel):
    caption: Caption


def rewrite_caption(ctx: AgentContext, current: str, others: list[str], instruction: str | None) -> tuple[str, list[str], bool]:
    """Write a replacement for one caption of a finished job.

    Returns (caption file text, unsupported-claim warnings, is_demo). Uses the same guardrails and
    verified business context as the original run, and avoids repeating the job's other captions.
    """
    p = ctx.task.params or {}
    lang = p.get("caption_language", "th")
    platform = p.get("platform", "facebook")
    route = ctx.use_route(route_llm(ctx.session, ctx.job, ctx.org))  # type: ignore[arg-type]
    if route.demo or route.openai is None:
        cap = Caption(
            text=("[DEMO] แคปชันตัวอย่างที่สร้างใหม่จากโหมดสาธิต ยังไม่ได้สร้างโดย AI" if lang == "th"
                  else "[DEMO] Replacement caption from demo mode, not AI-generated."),
            hashtags=["#DEMO"], call_to_action="[DEMO]",
        )
        return format_caption(cap), [], True
    others_block = "\n---\n".join(o.strip() for o in others if o.strip()) or "(none)"
    user = (
        f"{prompts.context_block(ctx.business_context, ctx.brand)}\n\n"
        f"Campaign command from the business owner:\n\"\"\"\n{ctx.job.command}\n\"\"\"\n\n"
        f"Rewrite one caption for {platform} in {'Thai' if lang == 'th' else 'English'}. "
        "Return it as `caption` (text, hashtags, call_to_action).\n"
        f"The caption being replaced:\n\"\"\"\n{current.strip()}\n\"\"\"\n"
        f"The campaign's other captions (use a different angle, do not repeat them):\n{others_block}\n"
        + (f"Instruction from the business owner for the new caption: {instruction.strip()}\n" if instruction else "")
    )
    out, usage = route.openai.complete_json(prompts.CONTENT, user, CaptionRewrite)
    ctx.record_usage("openai", "chat", {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}, usage.cost_usd)
    if lang == "th" and not has_thai(out.caption.text):
        raise AgentError("CONTENT_WRONG_LANGUAGE", "The caption was requested in Thai but is not Thai")
    return format_caption(out.caption), find_unsupported_claims([out.caption.text], ctx.business_context), False
