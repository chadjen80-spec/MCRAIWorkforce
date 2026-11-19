"""Planner Agent: master command → validated deliverable spec → deterministic task dependency graph."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.base import AgentContext, has_thai
from app.errors import AgentError
from app.providers.config import resolve_comfyui, resolve_firefly, resolve_openai
from app.services.router import external_ai_allowed, route_llm

MAX_IMAGES = 8
MAX_CAPTIONS = 20


class ImageSpec(BaseModel):
    count: int = Field(ge=1, le=50)
    concepts: list[str] = Field(default_factory=list)


class CaptionSpec(BaseModel):
    count: int = Field(ge=1, le=100)
    language: Literal["th", "en"] = "th"
    platform: str = "facebook"


class DocumentSpec(BaseModel):
    doc_type: Literal["proposal", "quotation", "campaign_brief", "marketing_report"] = "proposal"
    formats: list[Literal["pdf", "docx"]] = ["pdf", "docx"]


class VideoSpec(BaseModel):
    duration_seconds: int = Field(default=60, ge=5, le=600)
    aspect_ratio: Literal["16:9", "9:16"] | None = None


class DeliverableSpec(BaseModel):
    summary: str = ""
    language: Literal["th", "en"] = "th"
    images: ImageSpec | None = None
    captions: CaptionSpec | None = None
    youtube_metadata: bool = False
    document: DocumentSpec | None = None
    video: VideoSpec | None = None


class PlannedTask(BaseModel):
    key: str
    agent: Literal["content", "image", "document", "video", "qa"]
    title: str
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)  # must succeed
    waits_for: list[str] = Field(default_factory=list)  # soft: only needs to be finished
    priority: int = 5
    estimated_cost_usd: Decimal = Decimal("0")


class Plan(BaseModel):
    planner_mode: Literal["llm", "rules"]
    summary: str
    language: Literal["th", "en"]
    spec: DeliverableSpec
    tasks: list[PlannedTask]
    estimated_cost_usd: Decimal
    notes: list[str] = Field(default_factory=list)


# --- deterministic rule-based extraction (no AI provider required) -----------------
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10}
_NUM = r"(\d{1,3}|" + "|".join(_WORDS) + r")"
_IMG_EN = (
    r"(?:images?|pictures?|photos?|posters?|visuals?|thumbnails?|artworks?|banners?|graphics?"
    r"|illustrations?|infographics?|maps?|logos?|icons?|drawings?)"
)
_IMG_TH = r"(?:ภาพ|รูป|โปสเตอร์|แบนเนอร์|แผนที่|โลโก้|อินโฟกราฟิก|ไอคอน|กราฟิก)"
_CAP_EN = r"(?:captions?|posts?)"
_CAP_TH = r"(?:แคปชั่น|แคปชัน|โพสต์)"


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else _WORDS[token.lower()]


def _count(text: str, en: str, th: str) -> int | None:
    """Count for a deliverable noun: '3 promotional images', 'ภาพ 3 ภาพ', '3 ภาพ'. None when not mentioned."""
    m = re.search(rf"\b{_NUM}\s+(?:[A-Za-z-]+\s+){{0,3}}?{en}\b", text, re.I)
    if m:
        return _to_int(m[1])
    m = re.search(rf"(\d{{1,3}})\s*{th}", text) or re.search(rf"{th}[^\d\n]{{0,20}}?(\d{{1,3}})\s*(?:ภาพ|รูป|ชิ้น|แบบ|อัน|โพสต์|แคปชั่น|แคปชัน)", text)
    if m:
        return int(m[1])
    if re.search(rf"\b{en}\b", text, re.I) or re.search(th, text):
        return 1
    return None


def parse_command_rules(command: str, default_language: str) -> DeliverableSpec:
    text = command.strip()
    language: Literal["th", "en"] = "th" if (re.search(r"\bthai\b", text, re.I) or has_thai(text)) else (
        "en" if re.search(r"\benglish\b", text, re.I) else default_language)  # type: ignore[assignment]

    spec = DeliverableSpec(summary=text[:200], language=language)
    if (n := _count(text, _IMG_EN, _IMG_TH)) is not None:
        spec.images = ImageSpec(count=n)
    if (n := _count(text, _CAP_EN, _CAP_TH)) is not None:
        cap_lang: Literal["th", "en"] = language
        if re.search(r"\benglish\s+(?:[A-Za-z]+\s+)?captions?", text, re.I):
            cap_lang = "en"
        elif re.search(r"\bthai\s+(?:[A-Za-z]+\s+)?captions?", text, re.I):
            cap_lang = "th"
        platform = "facebook"
        for p in ("instagram", "tiktok", "line", "linkedin", "youtube"):
            if re.search(rf"\b{p}\b", text, re.I):
                platform = p
                break
        if re.search(r"facebook|เฟซบุ๊ก|เฟสบุ๊ค", text, re.I):
            platform = "facebook"
        spec.captions = CaptionSpec(count=n, language=cap_lang, platform=platform)

    if re.search(r"proposal|quotation|\bbrief\b|\breport\b|\bpdf\b|\bdocx\b|document|ข้อเสนอ|ใบเสนอราคา|เอกสาร|รายงาน", text, re.I):
        if re.search(r"quotation|ใบเสนอราคา", text, re.I):
            doc_type = "quotation"
        elif re.search(r"\bbrief\b|บรีฟ", text, re.I):
            doc_type = "campaign_brief"
        elif re.search(r"\breport\b|รายงาน", text, re.I) and not re.search(r"proposal|ข้อเสนอ", text, re.I):
            doc_type = "marketing_report"
        else:
            doc_type = "proposal"
        wants_pdf = bool(re.search(r"\bpdf\b", text, re.I))
        wants_docx = bool(re.search(r"\bdocx\b|\bword\b", text, re.I))
        formats = [f for f, w in (("pdf", wants_pdf), ("docx", wants_docx)) if w] or ["pdf", "docx"]
        spec.document = DocumentSpec(doc_type=doc_type, formats=formats)  # type: ignore[arg-type]

    if re.search(r"video|\bmp4\b|\bclip\b|\breel\b|วิดีโอ|วีดีโอ|คลิป", text, re.I):
        duration = 60
        if m := re.search(r"(\d{1,3})\s*-?\s*(?:seconds?|secs?|s\b|วินาที)", text, re.I):
            duration = int(m[1])
        elif m := re.search(r"(\d{1,2})\s*-?\s*(?:minutes?|mins?|นาที)", text, re.I):
            duration = int(m[1]) * 60
        spec.video = VideoSpec(duration_seconds=max(5, min(duration, 600)))
        spec.youtube_metadata = True
    if re.search(r"youtube", text, re.I):
        spec.youtube_metadata = True
    return spec


def image_cost_estimate(ctx: AgentContext, has_llm: bool) -> Decimal:
    """What one image is expected to cost, following the same order as the image router."""
    s = ctx.settings
    firefly = resolve_firefly(ctx.session, ctx.org.id)
    allowed = external_ai_allowed(ctx.job, ctx.org)[0]
    openai = resolve_openai(ctx.session, ctx.org.id)
    if firefly and allowed and firefly.priority == "first":
        return s.firefly_usd_per_image
    if openai and allowed and openai.image_priority == "first":
        return s.image_usd_per_image
    if resolve_comfyui(ctx.session, ctx.org.id) is not None:
        return Decimal("0")
    if firefly and allowed:
        return s.firefly_usd_per_image
    return s.image_usd_per_image if has_llm else Decimal("0")


# --- graph construction (always deterministic) ----------------------------------------
_DEFAULT_CONCEPTS = [
    "hero key visual: a confident small-business owner using a laptop with a friendly AI assistant interface glow",
    "team of SME staff collaborating around a tablet showing automated business workflows",
    "abstract modern technology background with flowing data lines and soft light, space for headline",
    "close-up of a smartphone showing a business dashboard on a tidy desk in a small shop",
    "bright modern Thai small-business storefront with subtle digital overlay elements",
    "illustration-style overview of AI agents handling content, documents and video for a business",
    "minimal product-style composition with geometric shapes and a glowing assistant icon",
    "busy entrepreneur relaxing while tasks complete automatically in the background",
]


def build_plan(ctx: AgentContext, spec: DeliverableSpec, mode: Literal["llm", "rules"]) -> Plan:
    s = ctx.settings
    job = ctx.job
    requested = set(job.requested_agents or [])
    notes: list[str] = []
    aspect = job.aspect_ratio

    has_llm = resolve_openai(ctx.session, ctx.org.id) is not None and external_ai_allowed(job, ctx.org)[0]
    llm_cost = s.llm_estimated_usd_per_call if has_llm else Decimal("0")
    image_cost = image_cost_estimate(ctx, has_llm)

    tasks: list[PlannedTask] = []
    content_key: str | None = None
    image_keys: list[str] = []

    wants_content = bool(spec.captions or spec.youtube_metadata) and "content" in requested
    if (spec.captions or spec.youtube_metadata) and "content" not in requested:
        notes.append("Captions/copy requested but the Content agent was not selected; skipped.")
    if wants_content:
        content_key = "content_copy"
        count = min(spec.captions.count, MAX_CAPTIONS) if spec.captions else 0
        if spec.captions and spec.captions.count > MAX_CAPTIONS:
            notes.append(f"Caption count capped at {MAX_CAPTIONS}.")
        title = f"{count} {'Thai' if (spec.captions and spec.captions.language == 'th') else 'English'} " \
                f"{(spec.captions.platform if spec.captions else 'social').title()} captions" if count else "Campaign copy"
        tasks.append(PlannedTask(
            key=content_key, agent="content", title=title, priority=3, estimated_cost_usd=llm_cost,
            params={"caption_count": count,
                    "caption_language": spec.captions.language if spec.captions else spec.language,
                    "platform": spec.captions.platform if spec.captions else "facebook",
                    "youtube_metadata": spec.youtube_metadata, "language": spec.language},
        ))

    if spec.images:
        if "image" not in requested:
            notes.append("Images requested but the Image agent was not selected; skipped.")
        else:
            count = min(spec.images.count, MAX_IMAGES)
            if spec.images.count > MAX_IMAGES:
                notes.append(f"Image count capped at {MAX_IMAGES}.")
            for i in range(count):
                concept = spec.images.concepts[i] if i < len(spec.images.concepts) and spec.images.concepts[i].strip() \
                    else _DEFAULT_CONCEPTS[i % len(_DEFAULT_CONCEPTS)]
                key = f"image_{i + 1}"
                image_keys.append(key)
                tasks.append(PlannedTask(
                    key=key, agent="image", title=f"Promotional image {i + 1} of {count}", priority=4,
                    estimated_cost_usd=image_cost,
                    params={"index": i + 1, "concept": concept[:500], "aspect_ratio": aspect},
                ))

    if spec.document:
        if "document" not in requested:
            notes.append("Document requested but the Document agent was not selected; skipped.")
        else:
            tasks.append(PlannedTask(
                key=f"document_{spec.document.doc_type}", agent="document",
                title=f"{spec.document.doc_type.replace('_', ' ').title()} ({', '.join(f.upper() for f in spec.document.formats)})",
                priority=5, estimated_cost_usd=llm_cost,
                params={"doc_type": spec.document.doc_type, "formats": spec.document.formats, "language": spec.language},
                waits_for=[content_key] if content_key else [],
            ))

    if spec.video:
        if "video" not in requested:
            notes.append("Video requested but the Video agent was not selected; skipped.")
        else:
            duration = max(15, min(spec.video.duration_seconds, 120))
            if duration != spec.video.duration_seconds:
                notes.append(f"Video duration adjusted to {duration}s (supported range 15-120s).")
            tts_cost = s.tts_usd_per_1k_chars * Decimal(duration * 15) / Decimal(1000) if has_llm else Decimal("0")
            tasks.append(PlannedTask(
                key="video_promo", agent="video", title=f"{duration}-second promotional video (MP4)", priority=6,
                estimated_cost_usd=llm_cost + tts_cost,
                params={"duration_seconds": duration, "aspect_ratio": spec.video.aspect_ratio or aspect,
                        "language": spec.language},
                waits_for=([content_key] if content_key else []) + image_keys,
            ))

    if not tasks:
        raise AgentError(
            "PLAN_EMPTY",
            "No supported deliverable was found in the command for the selected agents. "
            "Mention images, captions, a document (proposal/PDF) or a video.",
        )

    tasks.append(PlannedTask(
        key="qa_review", agent="qa", title="Quality assurance review", priority=9, estimated_cost_usd=llm_cost,
        params={}, waits_for=[t.key for t in tasks],
    ))

    total = sum((t.estimated_cost_usd for t in tasks), Decimal("0"))
    if total > job.budget_limit_usd:
        raise AgentError(
            "BUDGET_EXCEEDED",
            f"Estimated cost ${total:.2f} exceeds the job budget ${job.budget_limit_usd:.2f}. "
            "Raise the budget or request fewer deliverables.",
        )
    _validate_dag(tasks)
    return Plan(planner_mode=mode, summary=spec.summary or job.command[:200], language=spec.language, spec=spec,
                tasks=tasks, estimated_cost_usd=total, notes=notes)


def _validate_dag(tasks: list[PlannedTask]) -> None:
    keys = [t.key for t in tasks]
    if len(set(keys)) != len(keys):
        raise AgentError("PLAN_INVALID", "Duplicate task keys in plan")
    edges = {t.key: set(t.depends_on) | set(t.waits_for) for t in tasks}
    for key, deps in edges.items():
        if missing := deps - set(keys):
            raise AgentError("PLAN_INVALID", f"Task {key} depends on unknown tasks {sorted(missing)}")
    done: set[str] = set()
    while len(done) < len(keys):
        ready = [k for k in keys if k not in done and edges[k] <= done]
        if not ready:
            raise AgentError("PLAN_INVALID", "Dependency cycle in plan")
        done.update(ready)


def build_poster_plan(ctx: AgentContext, poster: dict[str, Any]) -> Plan:
    """A poster job: one image task per variant (artwork + typeset layout), then QA. No AI planning needed."""
    job = ctx.job
    has_llm = resolve_openai(ctx.session, ctx.org.id) is not None and external_ai_allowed(job, ctx.org)[0]
    image_cost = image_cost_estimate(ctx, has_llm)
    from app.agents.poster import uses_heroes

    own_art = bool(poster.get("art_asset_id")) or uses_heroes(poster)
    if own_art:
        image_cost = Decimal("0")  # the user's own artwork: nothing is generated
    count = 1 if own_art else int(poster.get("variants") or 1)  # same artwork = same poster
    art = poster.get("art") or ""
    tasks = [
        PlannedTask(key=f"poster_{i + 1}", agent="image", title=f"Event poster {i + 1} of {count}", priority=4,
                    estimated_cost_usd=image_cost,
                    params={"index": i + 1, "aspect_ratio": "4:5" if poster.get("template") == "tournament" else "9:16",
                            "concept": art, "poster": poster})
        for i in range(count)
    ]
    tasks.append(PlannedTask(key="qa_review", agent="qa", title="Quality assurance review", priority=9,
                             params={}, waits_for=[t.key for t in tasks]))
    total = sum((t.estimated_cost_usd for t in tasks), Decimal("0"))
    if total > job.budget_limit_usd:
        raise AgentError("BUDGET_EXCEEDED", f"Estimated cost ${total:.2f} exceeds the job budget ${job.budget_limit_usd:.2f}.")
    spec = DeliverableSpec(summary=f"Event poster: {poster.get('title', '')}", language=job.language,  # type: ignore[arg-type]
                           images=ImageSpec(count=count, concepts=[art] * count if art else []))
    return Plan(planner_mode="rules", summary=spec.summary, language=spec.language, spec=spec, tasks=tasks,
                estimated_cost_usd=total)


def run(ctx: AgentContext) -> dict[str, Any]:
    job = ctx.job
    if (job.options or {}).get("poster"):
        from app.services.router import Route

        ctx.use_route(Route("rules", "local", "event poster: fixed layout, no AI planning needed"))
        return {"plan": build_poster_plan(ctx, job.options["poster"]).model_dump(mode="json")}  # type: ignore[index]
    route = route_llm(ctx.session, job, ctx.org, required=False)
    mode: Literal["llm", "rules"]
    if route and route.openai:
        ctx.use_route(route)
        spec, usage = route.openai.complete_json(
            prompts.PLANNER,
            f"Default language: {job.language}\nSelected agents: {', '.join(job.requested_agents)}\n"
            f"Master command:\n\"\"\"\n{job.command}\n\"\"\"",
            DeliverableSpec, temperature=0.1,
        )
        ctx.record_usage("openai", "chat", {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                         usage.cost_usd)
        mode = "llm"
    else:
        from app.services.router import Route

        ctx.use_route(Route("rules", "local", "deterministic rule-based planner (no cloud LLM configured or permitted)"))
        spec = parse_command_rules(job.command, job.language)
        mode = "rules"
    plan = build_plan(ctx, spec, mode)
    return {"plan": plan.model_dump(mode="json")}
