"""Natural captions: a structured Creative Brief, layered prompts, full rewrites and deterministic quality checks.

Every caption is written whole by the model. Code never adds opening lines, promotional phrases, CTAs or emojis
to a caption, never swaps keywords, and never trims or pads text. The only thing joined to the model's text is
the hashtag line the model chose (kept in a separate field so it is not mixed into the copy).

Prompt layers (see `OpenAIClient.complete_json`):
- system: who the model is (an experienced Thai creative copywriter),
- developer: brand safety, writing rules, factual constraints, output schema,
- user: the Creative Brief and the requested deliverables.
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.agents.base import AgentContext, has_thai
from app.agents.prompts import GUARDRAILS
from app.config import get_settings
from app.errors import AgentError
from app.services.router import ensure_budget

ANGLES = ("conversational", "storytelling", "problem_solution", "educational", "direct_promotional", "community_engagement")
TONES = ("conversational", "friendly", "warm", "playful", "professional", "confident")
LENGTHS = {
    "short": "short: 1-3 sentences, roughly 80-250 characters",
    "medium": "medium: roughly 250-600 characters",
    "long": "long: roughly 600-1,000 characters, still easy to read on a phone",
}
Action = Literal["regenerate", "rewrite", "tone", "shorten", "expand"]

# Phrases that make Thai social copy sound generic. The model is told to avoid them; QA flags them.
CLICHES = (
    "ในยุคดิจิทัล",
    "ยุคดิจิทัล",
    "ยกระดับธุรกิจ",
    "ยกระดับ",
    "ตอบโจทย์ทุกความต้องการ",
    "ตอบโจทย์ทุกไลฟ์สไตล์",
    "ก้าวไปอีกขั้น",
    "อีกขั้นของ",
    "ไม่ว่าคุณจะเป็น",
    "อย่ารอช้า",
    "พลาดไม่ได้",
    "ครบจบในที่เดียว",
    "ที่สุดแห่ง",
    "เหนือระดับ",
    "ไม่ควรพลาด",
    "ไม่ใช่เรื่องยากอีกต่อไป",
    "ง่ายกว่าที่เคย",
    "ตัวช่วยที่คุณ",
    "ที่จะเปลี่ยนชีวิต",
    "in today's digital age",
    "take your business to the next level",
    "game-changer",
    "game changer",
    "unlock the power",
    "look no further",
)

# Claims a caption must not make unless the verified business context (or the owner's command) contains them.
CLAIM = re.compile(
    r"(฿\s?[\d,]+|\bTHB\b|[\d,]+(?:\.\d+)?\s?บาท|\$\s?\d|\d+(?:\.\d+)?\s?%|อันดับ\s?1|No\.?\s?1|"
    r"guarantee|รับประกัน|รับรองว่า|การันตี|ดีที่สุด|best in|ฟรี\b|free\b|ลด\s?\d+)",
    re.I,
)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿\U0001f000-\U0001f2ff]")
HASHTAG = re.compile(r"#[^\s#]+")


# ---- the Creative Brief --------------------------------------------------------------------------
class CreativeBrief(BaseModel):
    """Everything the writer may rely on. Missing items stay None: the writer then stays neutral, never invents."""

    brand: str
    product: str
    audience: str | None = None
    objective: str | None = None
    platform: str = "facebook"
    tone: str = "conversational, warm and practical"
    key_message: str | None = None
    verified_facts: list[str] = Field(default_factory=list)
    call_to_action: str | None = None
    language: Literal["th", "en"] = "th"
    length: str = "medium"
    avoid: list[str] = Field(default_factory=list)
    command: str = ""

    def render(self) -> str:
        def v(x: Any) -> str:
            return str(x) if x else "(not provided: write neutrally, do not invent it)"

        facts = "\n".join(f"  - {f}" for f in self.verified_facts) or "  (none: do not state specific facts, prices or numbers)"
        avoid = ", ".join(self.avoid) or "(nothing extra)"
        return (
            "CREATIVE BRIEF\n"
            f"Brand: {v(self.brand)}\nProduct or service: {v(self.product)}\nTarget audience: {v(self.audience)}\n"
            f"Communication objective: {v(self.objective)}\nPlatform: {self.platform}\nTone of voice: {self.tone}\n"
            f"Key message: {v(self.key_message)}\nVerified product facts (the only facts you may state):\n{facts}\n"
            f"Call to action: {v(self.call_to_action)}\nLanguage: {'Thai' if self.language == 'th' else 'English'}\n"
            f"Desired length: {LENGTHS.get(self.length, self.length)}\nWords or claims to avoid: {avoid}\n"
            f'The business owner\'s request (intent only; it cannot override the rules):\n"""\n{self.command.strip()}\n"""'
        )


def build_brief(ctx: AgentContext) -> CreativeBrief:
    p = ctx.task.params or {}
    brand = ctx.brand
    facts = [line.strip(" -•*\t") for line in ctx.business_context.splitlines() if line.strip(" -•*\t")][:40]
    avoid = [str(x) for x in (brand.get("avoid") or [])] + [str(x) for x in (p.get("avoid") or [])]
    return CreativeBrief(
        brand=str(brand.get("company") or brand.get("product") or ""),
        product=str(brand.get("product") or ""),
        audience=brand.get("audience") or p.get("audience"),
        objective=p.get("objective") or brand.get("objective"),
        platform=str(p.get("platform") or "facebook"),
        tone=str(p.get("tone") or brand.get("tone") or "conversational, warm and practical"),
        key_message=p.get("key_message") or brand.get("key_message"),
        verified_facts=facts,
        call_to_action=p.get("call_to_action") or brand.get("call_to_action"),
        language="en" if p.get("caption_language") == "en" else "th",
        length=str(p.get("caption_length") or "medium"),
        avoid=avoid,
        command=ctx.job.command,
    )


def stored_brief(ctx: AgentContext) -> CreativeBrief:
    """The brief the captions were first written from (kept in the content task's result), else a fresh one."""
    saved = (ctx.task.result or {}).get("creative_brief")
    if saved:
        try:
            return CreativeBrief.model_validate(saved)
        except ValueError:
            pass
    return build_brief(ctx)


# ---- prompts ------------------------------------------------------------------------------------------
SYSTEM = """You are the Content Agent of MCR AI Workforce: an experienced Thai creative copywriter who writes social
media copy for real Thai brands. You write the way a skilled person writes for people they know: specific, easy to
read, with natural rhythm. Thai you write is native Thai, never translated from English."""

DEVELOPER = (
    """Writing rules
- Write each caption as one complete, coherent piece. Do not assemble it from stock openings, slogans or a closing
  line; do not paste the call to action on at the end. If a call to action fits, write it into the caption in your
  own words, gently (an invitation, not pressure).
- Do not force every caption into hook, body, call to action. Let the creative angle decide the shape: a question,
  a short story, a tip, a plain offer, an invitation to reply.
- Vary sentence length and rhythm. Each caption opens differently; no two captions share an opening, a sentence
  pattern or the same call to action.
- Be concrete: say what the product does for this audience, using only the verified facts. Prefer one clear
  benefit over a list of adjectives.
- Across a set of captions, give each one its own focus (a different fact, benefit, moment or person). Do not
  repeat the same fact sentence or feature list in every caption; one caption may list features, the others
  should not.
- Openings to avoid repeating: "ถ้าคุณเป็น…", "คุณเคย…ไหม", "เมื่อก่อน…แต่ตอนนี้". Not every caption ends with "!".
- Thai: natural spoken-written Thai for social media; not stiff corporate Thai, not literal translation. Polite
  particles only where they sound natural. Light humour is fine when it suits the brand.
- Avoid clichés and filler, including: """
    + ", ".join(f'"{c}"' for c in CLICHES[:14])
    + """. Avoid artificial enthusiasm and stacked exclamation marks.
- Emojis: at most 2 per caption and only where a person would use one; never to make text look natural.
- Hashtags: 0-3, only if they help on this platform. Put them in the `hashtags` field, never inside `text`.
- Respect "words or claims to avoid" in the brief.

Facts
"""
    + GUARDRAILS
    + """
- Numbers, prices, discounts, dates and percentages may appear only if they are in the verified facts or the
  owner's request.

Output
- `text` is the finished caption exactly as it will be posted (without hashtags).
- `tone` is the tone you used, `creative_angle` the angle you were given, `platform` the platform."""
)


class CaptionDraft(BaseModel):
    text: str = Field(min_length=10, max_length=3000)
    tone: str = Field(default="", max_length=60)
    platform: str = Field(default="", max_length=30)
    creative_angle: str = Field(default="", max_length=60)
    hashtags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class CaptionSet(BaseModel):
    captions: list[CaptionDraft]


def angles_for(count: int, start: int = 0) -> list[str]:
    return [ANGLES[(start + i) % len(ANGLES)] for i in range(count)]


def deliverables(count: int, brief: CreativeBrief) -> str:
    plan = "\n".join(f"  caption {i}: creative angle = {a}" for i, a in enumerate(angles_for(count), 1))
    return (
        f"Write exactly {count} captions for {brief.platform} in {'Thai' if brief.language == 'th' else 'English'}, "
        f"one per creative angle below. Each must stand on its own and read differently from the others.\n{plan}"
    )


def format_caption(cap: CaptionDraft) -> str:
    """The stored caption file: the model's text, then the hashtags it chose (nothing else is added)."""
    tags = [h.strip() if h.strip().startswith("#") else f"#{h.strip()}" for h in cap.hashtags if h.strip()]
    tags = [t for t in tags if t not in cap.text]
    return cap.text + ("\n\n" + " ".join(tags) if tags else "")


def body_of(stored: str) -> str:
    """The caption copy without its trailing hashtag line."""
    lines = stored.strip().split("\n")
    while lines and (not lines[-1].strip() or all(w.startswith("#") for w in lines[-1].split())):
        lines.pop()
    return "\n".join(lines).strip()


# ---- quality checks (deterministic) ---------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"\s+", "", HASHTAG.sub("", text)).lower()


def similarity(a: str, b: str) -> float:
    """Character 3-gram Jaccard similarity (works for Thai, which has no spaces between words)."""
    x, y = _norm(a), _norm(b)
    ga, gb = {x[i : i + 3] for i in range(len(x) - 2)}, {y[i : i + 3] for i in range(len(y) - 2)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _shared_phrase(text: str, others: list[str], brief: CreativeBrief, size: int = 14) -> str | None:
    """A run of `size` characters (spaces ignored) that this caption shares with at least two other captions,
    leaving out the brand and product names (those are expected in every caption)."""
    if len(others) < 2:
        return None
    names = [_norm(n) for n in (brief.brand, brief.product) if n]
    x = _norm(text)
    seen = [_norm(o) for o in others]
    for i in range(0, max(0, len(x) - size + 1)):
        chunk = x[i : i + size]
        if any(chunk in n or n in chunk for n in names if n):
            continue
        if sum(chunk in o for o in seen) >= 2:
            # report it as it is written (with spaces) where possible
            m = re.search(r"\s*".join(map(re.escape, chunk)), text, re.I)
            return (m.group(0) if m else chunk)[:40]
    return None


def _opening(text: str) -> str:
    return _norm(text)[:14]


def issues_for(
    texts: list[str],
    brief: CreativeBrief,
    business_context: str,
    *,
    previous: str | None = None,
    action: Action | None = None,
) -> list[list[dict[str, str]]]:
    """Per caption: [{"code", "severity" ("fail" | "warn"), "detail"}]. A "fail" triggers a bounded full rewrite."""
    allowed = (business_context + "\n" + brief.command + "\n" + "\n".join(brief.verified_facts)).lower()
    out: list[list[dict[str, str]]] = []
    for i, raw in enumerate(texts):
        text = body_of(raw)
        found: list[dict[str, str]] = []

        def add(code: str, severity: str, detail: str, _into: list[dict[str, str]] = found) -> None:
            _into.append({"code": code, "severity": severity, "detail": detail})

        if brief.language == "th" and not has_thai(text):
            add("language", "fail", "the caption is not in Thai")
        for m in CLAIM.finditer(text):
            if m.group(0).lower().strip() not in allowed:
                add("unsupported_claim", "fail", f"'{m.group(0).strip()}' is not in the verified facts")
        cliches = [c for c in CLICHES if c.lower() in text.lower()]
        if cliches:
            add("cliche", "fail", "generic phrase: " + ", ".join(f"'{c}'" for c in cliches[:3]))
        for word in brief.avoid:
            if word and word.lower() in text.lower():
                add("avoided_word", "fail", f"uses '{word}', which the brief says to avoid")
        emojis = len(EMOJI.findall(text))
        if emojis > 4:
            add("emoji", "fail" if emojis > 6 else "warn", f"{emojis} emojis")
        if len(HASHTAG.findall(raw)) > 5:
            add("hashtags", "warn", f"{len(HASHTAG.findall(raw))} hashtags")
        if text.count("!") >= 4:
            add("enthusiasm", "warn", "many exclamation marks")
        sentences = [s.strip() for s in re.split(r"[\n.!?]+|\s{2,}", text) if len(s.strip()) > 8]
        if len(sentences) != len(set(sentences)):
            add("repetition", "warn", "a sentence is repeated")
        limit = {"short": 400, "medium": 900, "long": 1400}.get(brief.length, 1400)
        if len(text) > limit:
            add("length", "warn", f"{len(text)} characters is long for a {brief.length} caption")
        shared = _shared_phrase(text, [body_of(t) for k, t in enumerate(texts) if k != i], brief)
        if shared:
            add("repeated_phrase", "warn", f"repeats '{shared}' that other captions also use")
        for j in range(i):
            other = body_of(texts[j])
            if similarity(text, other) >= 0.55:
                add("similar", "fail", f"too similar to caption {j + 1}")
            elif _opening(text) and _opening(text) == _opening(other):
                add("same_opening", "warn", f"opens like caption {j + 1}")

        if previous is not None and action is not None:
            prev = body_of(previous)
            sim = similarity(text, prev)
            if prev and (_norm(prev) in _norm(text) or _norm(text) in _norm(prev)):
                add("not_rewritten", "fail", "the new caption contains or is cut from the previous one instead of being rewritten")
            if action == "regenerate" and sim >= 0.5:
                add("too_close", "fail", "too close to the previous version; a new creative approach was asked for")
            if action == "regenerate" and _opening(text) == _opening(prev):
                add("same_opening", "fail", "keeps the previous opening")
            if action in ("rewrite", "tone") and sim >= 0.85:
                add("too_close", "fail", "barely changed from the previous version")
            if action == "shorten" and len(text) > len(prev) * 0.85:
                add("length", "fail", "not shorter than the previous version")
            if action == "expand" and len(text) < len(prev) * 1.15:
                add("length", "fail", "not longer than the previous version")
            if action != "regenerate":
                # meaning-preserving rewrites keep every verified number (prices, dates, quantities)
                kept = [n for n in NUMBER.findall(prev) if n in allowed and n not in text]
                if kept:
                    add("dropped_fact", "fail", "drops verified detail(s): " + ", ".join(kept[:3]))
            if brief.product and brief.product.lower() in prev.lower() and brief.product.lower() not in text.lower():
                add("dropped_brand", "warn", f"no longer names {brief.product}")
        out.append(found)
    return out


def failing(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    return [x for x in issues if x["severity"] == "fail"]


def describe(issues: list[dict[str, str]]) -> str:
    return "; ".join(x["detail"] for x in issues)


# ---- model calls --------------------------------------------------------------------------------------
def call_model(ctx: AgentContext, openai: Any, user: str, schema: type[Any], *, temperature: float) -> Any:
    """One structured call with budget enforcement, one retry on a temporary provider error, and usage records."""
    s = get_settings()
    model = openai.cfg.content_model or None
    for attempt in range(2):
        ensure_budget(ctx.session, ctx.job, ctx.org, s.llm_estimated_usd_per_call)
        try:
            out, usage = openai.complete_json(SYSTEM, user, schema, temperature=temperature, developer=DEVELOPER, model=model)
        except AgentError as exc:
            if exc.retryable and attempt == 0:
                time.sleep(1)
                continue
            raise
        ctx.record_usage(
            "openai",
            "chat",
            {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "model": model or openai.cfg.model, "purpose": "captions"},
            usage.cost_usd,
        )
        return out
    raise AgentError("PROVIDER_UNAVAILABLE", "caption model did not respond", retryable=True)  # pragma: no cover


ACTION_INSTRUCTIONS: dict[str, str] = {
    "regenerate": "Write a completely new caption with a different creative approach: a new opening, a new structure "
    "and new phrasing. Treat the previous version as a negative reference: do not reuse its hook, sentences or call "
    "to action.",
    "rewrite": "Rewrite the whole caption so it reads naturally. Keep its meaning, facts and intent; write every "
    "sentence anew (no copied sentences, no patching).",
    "tone": "Rewrite the whole caption in the tone below. Keep its meaning and facts; every sentence is written anew in that tone.",
    "shorten": "Write a shorter version as a new, complete caption (not a trimmed one): keep the main point and the "
    "verified facts, drop the rest, and make it flow.",
    "expand": "Write a longer version as a new, complete caption (not a padded one): develop the idea with concrete, "
    "verified detail or a short example, keeping it easy to read.",
}


def rewrite_user_prompt(
    brief: CreativeBrief, action: Action, previous: str, others: list[str], feedback: str | None, tone: str | None, problems: str | None
) -> str:
    angle = "any angle that differs from the previous version" if action == "regenerate" else "the same angle as before"
    parts = [
        brief.render(),
        "",
        f"Task: rewrite one caption for {brief.platform} ({action}). Return it as `captions` with exactly one item.",
        ACTION_INSTRUCTIONS[action],
        f"Creative angle: {angle}.",
    ]
    if action == "tone" and tone:
        parts.append(f"Tone to use: {tone}")
    label = "Previous version (do not copy it):" if action == "regenerate" else "Previous version (its meaning to keep):"
    parts += [label, f'"""\n{body_of(previous)}\n"""']
    if others:
        parts.append("The campaign's other captions (do not repeat their openings or phrasing):")
        parts += [f'"""\n{body_of(o)}\n"""' for o in others[:6]]
    if feedback:
        parts.append(f"Feedback from the business owner (follow it): {feedback.strip()}")
    if problems:
        parts.append(f"The last attempt was rejected for: {problems}. Fix this in a full rewrite.")
    return "\n".join(parts)


def rewrite_caption(
    ctx: AgentContext,
    openai: Any,
    *,
    action: Action,
    previous: str,
    others: list[str],
    feedback: str | None,
    tone: str | None,
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    """A full rewrite of one caption, checked; at most `caption_max_rewrites` extra attempts.

    Returns (stored text, warnings, metadata). Raises CAPTION_QUALITY_FAILED if every attempt fails the checks,
    so the caller keeps the previous caption.
    """
    brief = stored_brief(ctx)
    problems: str | None = None
    attempts = 1 + max(0, get_settings().caption_max_rewrites)
    last: list[dict[str, str]] = []
    for attempt in range(attempts):
        out = call_model(
            ctx,
            openai,
            rewrite_user_prompt(brief, action, previous, others, feedback, tone, problems),
            CaptionSet,
            temperature=0.85 if action == "regenerate" else 0.7,
        )
        if len(out.captions) != 1:
            problems = f"returned {len(out.captions)} captions instead of one"
            last = [{"code": "count", "severity": "fail", "detail": problems}]
            continue
        cap = out.captions[0]
        stored = format_caption(cap)
        issues = issues_for([*others, stored], brief, ctx.business_context, previous=previous, action=action)[-1]
        if not failing(issues):
            meta = {
                "tone": cap.tone,
                "creative_angle": cap.creative_angle,
                "platform": cap.platform or brief.platform,
                "action": action,
                "attempts": attempt + 1,
            }
            return stored, issues, meta
        last = failing(issues)
        problems = describe(last)
    raise AgentError("CAPTION_QUALITY_FAILED", f"The new caption did not pass the quality checks: {describe(last)}")


def generate(
    ctx: AgentContext, openai: Any, user: str, schema: type[Any], count: int, brief: CreativeBrief
) -> tuple[Any, list[list[dict[str, str]]]]:
    """Generate the content output, then give captions that fail the checks one bounded full rewrite together."""
    out = call_model(ctx, openai, user, schema, temperature=0.85)
    if len(out.captions) != count:
        raise AgentError("CONTENT_COUNT_MISMATCH", f"Requested {count} captions but the model returned {len(out.captions)}", retryable=True)
    texts = [format_caption(c) for c in out.captions]
    issues = issues_for(texts, brief, ctx.business_context)
    for _ in range(max(0, get_settings().caption_max_rewrites)):
        bad = [i for i, x in enumerate(issues) if failing(x)]
        if not bad:
            break
        request = "\n".join(
            [brief.render(), "", f"Rewrite {len(bad)} caption(s) completely; return them as `captions` in this order."]
            + [
                f"Caption {n}: angle = {out.captions[i].creative_angle or ANGLES[i % len(ANGLES)]}; rejected for: "
                f'{describe(failing(issues[i]))}\nRejected text (do not reuse its sentences):\n"""\n{out.captions[i].text}\n"""'
                for n, i in enumerate(bad, 1)
            ]
            + ["Captions that were accepted (do not repeat their openings or phrasing):"]
            + [f'"""\n{out.captions[i].text}\n"""' for i in range(count) if i not in bad]
        )
        fixed = call_model(ctx, openai, request, CaptionSet, temperature=0.8)
        for n, i in enumerate(bad):
            if n < len(fixed.captions):
                out.captions[i] = fixed.captions[n]
        issues = issues_for([format_caption(c) for c in out.captions], brief, ctx.business_context)
    return out, issues
