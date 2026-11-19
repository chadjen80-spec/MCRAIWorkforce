"""Poster design chat: talk to the assistant (Mira) and she fills in the poster form for you.

The model only edits the *draft* shown in the form; nothing is generated or spent on images here.
Logos and artwork are referenced by their library names and mapped to ids on the server, so the model
can never invent an asset id. Creating the poster is still an explicit click by the user.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.config import get_settings
from app.db import get_db
from app.deps import Principal, get_principal, rate_limited
from app.errors import AgentError, AppError
from app.models import BrandAsset, UsageEvent
from app.providers.config import resolve_openai
from app.services.budget import committed_this_month
from app.services.router import make_openai

router = APIRouter(prefix="/v1/posters", tags=["posters"])

SYSTEM = """You are Mira, the friendly poster-design assistant of MCR AI Workforce (Poster Designer).
You help a Thai SME owner design an event poster. There are two layouts ("template"):
- "esports" (9:16): sponsor logo bar at the top, event title, a white date banner, 1-3 gold headline lines, venue
  with a pin, a big time line, and presenter logos at the bottom.
- "tournament" (4:5): a competition/registration poster. Key art at the top with the event logo top-left, the
  organisers' logos top-right and the game's logo in the middle; then the project name, the prize line, a huge gold
  prize amount, the registration deadline, a red "free entry" pill beside the category lines, a red schedule card
  (1-2 columns), a QR code to register, the venue with a photo, and a black sponsor strip at the bottom.
  Choose it when the user mentions prize money, registration/สมัคร, a QR code or a schedule card, or asks for it.
The AI paints only the background artwork; the app typesets every word and places the user's own logos.

You edit the poster draft the user sees next to the chat. Every turn, return the WHOLE updated draft plus a reply.
Rules:
- Keep everything the user did not ask to change. Apply exactly what they ask (theme, wording, lines, logos...).
- Never invent facts: event name, date, time, venue, round or prize must come from the user. If a needed fact is
  missing, leave the field empty and ask for it in your reply (ask at most two things at a time).
- Write poster text in the language the user uses (Thai stays Thai). Keep lines short: title <= 40 characters,
  each headline line <= 48, date <= 60, venue <= 80, time <= 40, at most 3 headline lines.
- Logos and artwork: use ONLY names from the library list, exactly as written. If the user names something that is
  not in the library, say so and suggest uploading it. Never put a logo in two places.
- "artwork": the name of the user's own uploaded artwork to use, or null to let the AI paint it. When null, write
  "art": a short English description of an ORIGINAL character/scene. Never describe a hero, character, logo or
  art style from an existing game or franchise (e.g. ROV/Arena of Valor heroes); if the user asks for one, explain
  briefly that its art belongs to the publisher and describe an original look-alike-free alternative instead.
- "theme" is one of gold, neon, crimson. "variants" 1-3 (only 1 with own artwork).
  Colours: gold = gold/yellow/orange/warm; neon = blue/cyan/navy/purple/cool (น้ำเงิน, ฟ้า, ม่วง → neon);
  crimson = red/pink/maroon. If the user asks for a colour, switch to the closest theme and say which one you chose
  (e.g. "ไม่มีโทนสีน้ำเงินโดยตรง จึงเลือกโทนนีออนซึ่งเป็นฟ้า-น้ำเงินให้ค่ะ"). Never claim a theme you did not set.

What each field is for (put every detail in its own field, never in another one):
- "tag": the small line above the title, usually the game or organiser (e.g. "ROV").
- "title": the event name only, WITHOUT the year (e.g. "Valor City").
- "edition": the year or edition shown in the badge under the title (e.g. "2026").
- "date_text": the date as it should read on the white banner (e.g. "วันอาทิตย์ที่ 3 พฤษภาคม 2569").
- "headline": 1-3 lines about the competition itself: stage, round, region, bracket. NEVER the date, time or venue.
- "venue": the place, with floor or zone if given.
- "time_text": the start time line (e.g. "10.00 น. เป็นต้นไป" or "From 10:00").
- "art": always fill it when "artwork" is null: one vivid English sentence describing an ORIGINAL character, pose,
  outfit, weapon and backdrop in the theme's colours (never generic wording such as "fitting the theme").
  Match the game's genre (fighting game -> martial artist; MOBA -> fantasy hero; football -> footballer...).
- Character / artwork requests ("เพิ่มตัวละคร", "ใส่อาร์ตเวิร์ค", "add a character", "เปลี่ยนตัวละคร"...): if the
  user names an uploaded artwork, set "artwork" to it. Otherwise write a NEW, clearly different "art" character
  (never return the same "art" again when asked for a character), using any look the user describes.
  In the reply, say in the user's language what character will be painted (1 short sentence) and that the AI paints
  it when they press "Create poster" - the preview only shows a stand-in figure until then.
- When you decline a request (e.g. an existing game's hero), say why in a few words: the character's art is
  owned by its publisher, so the poster could not be published safely.
Tournament fields (template "tournament"; leave them empty for "esports"). In the tournament layout the esports
fields "tag", "date_text", "headline" and "time_text" are NOT shown: leave them empty and put every detail in the
tournament field it belongs to (who can enter -> "category_lines", times -> "schedule" lines).
- "presenter_logos" are the organisers' logos shown top-right (no label). "sponsor_logos" go in the black strip.
- "game_logo": the game's logo from the library, centred over the art (only a logo the user uploaded).
- "subtitle": the project name line (e.g. "โครงการจัดแข่งขันกีฬาอีสปอร์ตจังหวัดสุราษฎร์ธานี").
- "prize_label": the line above the amount (e.g. "ชิงเงินรางวัลรวมกว่า"); "prize_amount": e.g. "8,000 บาท".
- "registration_text": e.g. "เปิดรับสมัครถึงวันที่ 6 สิงหาคม 2569 (ออนไลน์)".
- "badge_text": the red pill, e.g. "สมัครฟรี! ไม่มีค่าใช้จ่าย" (only if entry is free or the user gives it).
- "category_lines": 1-2 lines (<= 60 chars) about who can enter; wrap the key part in *asterisks* to highlight it
  in yellow, e.g. ["รุ่นประชาชนทั่วไป *ประเภทบุคคล (ไม่จำกัดเพศ)*", "อายุ 13 ปี - 60 ปี (จำกัด 16 ทีม)"].
- "schedule": 1-2 columns {"label", "big", "lines"}: label = the header (<= 40), big = the day number(s) (<= 8,
  e.g. "7" or "8-9"), lines = up to 2 short lines (<= 24) such as ["สิงหาคม 2569", "เวลา 17.00 น."].
  Only times and weekdays the user actually gave: if they gave no time for a column, its lines are just the
  month and year (e.g. ["สิงหาคม", "2569"]). Never fill a gap with a typical time such as "10.00 น.".
- "cta_text": above the QR code, e.g. "สมัครเลย". "qr_url": the registration link exactly as the user gave it
  (https://...); never invent one, leave it empty and ask.
- "venue" is the big venue line (e.g. "ณ ลานโปรโมชัน ชั้น 1"); "venue_detail" the line under it (e.g. the mall).
- "venue_photo": the name of a venue photo in the artwork library, or null.
- "hero_left" / "hero_right": names of character images from the library (logo or artwork) standing left (blue
  side) and right (red side) of the orb, like the series posters. With either one set, no AI art is painted.
  "สลับตัวละคร" swaps them. Only images the user uploaded; remind them to use art they have permission for.
- The title/edition are only shown when there is no event logo.
- "art" for this layout: two ORIGINAL rival characters facing the centre (blue left, red right) around an orb.
Example: "ทำโปสเตอร์แข่ง ROV ชื่อ Valor City 2026 รอบ 8 ทีมสุดท้ายภาคใต้ตอนบน สายสุราษฎร์ธานี วันที่ 3 พ.ค. 2569
ที่โลตัสสุราษฎร์ธานี ชั้น 3 เริ่ม 10 โมง" → tag "ROV", title "Valor City", edition "2026",
headline ["ภาคใต้ตอนบน รอบ 8 ทีมสุดท้าย", "สายแข่งศูนย์สุราษฎร์ธานี"], date_text "วันอาทิตย์ที่ 3 พฤษภาคม 2569"
(only add the weekday if the user gave it), venue "ห้างโลตัส สุราษฎร์ธานี (ชั้น 3)", time_text "10.00 น. เป็นต้นไป".
- "ready" is true only when the title and the key details the user wants are filled in.
- Artwork position: "art_zoom" (1 = fills the frame; 0.5-2.5), "art_x" and "art_y" (-0.6..0.6, a share of the
  frame; positive x = right, positive y = down). Change them by small steps from the CURRENT values:
  move down/ลง/ลงล่าง -> art_y + 0.1; up/ขึ้น -> art_y - 0.1; left/ซ้าย -> art_x - 0.1; right/ขวา -> art_x + 0.1;
  "a lot"/"เยอะๆ" doubles the step; bigger/ขยาย/ใหญ่ขึ้น -> art_zoom x 1.15; smaller/ย่อ/เล็กลง -> art_zoom / 1.15;
  reset/กลับที่เดิม -> 1, 0, 0. Say what you moved in the reply. This works for AI art and the user's own artwork.
- "reply": REQUIRED, never empty: 1-3 short, warm sentences in the user's language: what you changed and what
  (if anything) you need.
  Do not claim the poster was created: the user presses "Create poster" themselves."""


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1500)


class PosterDraft(BaseModel):
    """The form as it is now (the title may still be empty)."""

    model_config = ConfigDict(extra="ignore")

    theme: Literal["gold", "neon", "crimson"] = "gold"
    tag: str = ""
    title: str = ""
    edition: str = ""
    date_text: str = ""
    headline: list[str] = Field(default_factory=list)
    venue: str = ""
    time_text: str = ""
    art: str = ""
    art_asset_id: str | None = None
    event_logo_id: str | None = None
    sponsor_logo_ids: list[str] = Field(default_factory=list)
    presenter_label: str = "Presented by"
    presenter_logo_ids: list[str] = Field(default_factory=list)
    variants: int = 1
    template: Literal["esports", "tournament"] = "esports"
    game_logo_id: str | None = None
    subtitle: str = ""
    prize_label: str = ""
    prize_amount: str = ""
    registration_text: str = ""
    badge_text: str = ""
    category_lines: list[str] = Field(default_factory=list)
    schedule: list[dict] = Field(default_factory=list)
    cta_text: str = ""
    qr_url: str = ""
    venue_detail: str = ""
    venue_photo_id: str | None = None
    art_zoom: float = 1.0
    art_x: float = 0.0
    art_y: float = 0.0
    hero_left_id: str | None = None
    hero_right_id: str | None = None


class PosterChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=30)
    draft: PosterDraft = Field(default_factory=PosterDraft)
    language: Literal["th", "en"] = "th"


class ScheduleDraft(BaseModel):
    label: str = ""
    big: str = ""
    lines: list[str] = Field(default_factory=list)


class ModelDraft(BaseModel):
    """What the model returns: the draft with assets referenced by library name."""

    reply: str = ""
    theme: str = "gold"
    tag: str = ""
    title: str = ""
    edition: str = ""
    date_text: str = ""
    headline: list[str] = Field(default_factory=list)
    venue: str = ""
    time_text: str = ""
    art: str = ""
    artwork: str | None = None
    event_logo: str | None = None
    sponsor_logos: list[str] = Field(default_factory=list)
    presenter_label: str = "Presented by"
    presenter_logos: list[str] = Field(default_factory=list)
    variants: int = 1
    template: str = "esports"
    game_logo: str | None = None
    subtitle: str = ""
    prize_label: str = ""
    prize_amount: str = ""
    registration_text: str = ""
    badge_text: str = ""
    category_lines: list[str] = Field(default_factory=list)
    schedule: list[ScheduleDraft] = Field(default_factory=list)
    cta_text: str = ""
    qr_url: str = ""
    venue_detail: str = ""
    venue_photo: str | None = None
    art_zoom: float = 1.0
    art_x: float = 0.0
    art_y: float = 0.0
    hero_left: str | None = None
    hero_right: str | None = None
    ready: bool = False


class PosterChatResponse(BaseModel):
    reply: str
    draft: dict
    ready: bool
    unknown_assets: list[str] = Field(default_factory=list)


def _clip(text: str, n: int) -> str:
    return " ".join((text or "").split())[:n]


_WEEKDAYS_TH = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "พฤหัส", "ศุกร์", "เสาร์", "อาทิตย์")
_WEEKDAYS_EN = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _drop_invented_weekday(date_text: str, said: str) -> str:
    """Remove a weekday the user never said (the model likes to add one, and it is often wrong)."""
    said = said.casefold()
    for day in _WEEKDAYS_TH:
        if f"วัน{day}" in date_text and day not in said:
            return " ".join(re.sub(rf"วัน{day}(ที่)?\s*", "วันที่ ", date_text, count=1).split())
    low = date_text.casefold()
    for day in _WEEKDAYS_EN:
        if day in low and day not in said and day[:3] not in said:
            return " ".join(re.sub(rf"(?i){day},?\s*", "", date_text, count=1).split())
    return date_text


def _num(value: object, low: float, high: float, default: float) -> float:
    try:
        return round(min(high, max(low, float(value))), 3)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _url(text: str) -> str:
    url = _clip(text, 300)
    return url if url.lower().startswith(("https://", "http://")) else ""


def _schedule(item: ScheduleDraft | dict) -> dict:
    x = item.model_dump() if isinstance(item, ScheduleDraft) else item
    return {"label": _clip(x.get("label", ""), 40), "big": _clip(x.get("big", ""), 8),
            "lines": [_clip(ln, 24) for ln in (x.get("lines") or []) if str(ln).strip()][:2]}


@router.post("/chat", response_model=PosterChatResponse, summary="Design a poster by chatting with the assistant")
def poster_chat(body: PosterChatRequest, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    settings = get_settings()
    org = principal.organization
    rate_limited(f"poster-chat:{org.id}", settings.assistant_speech_per_minute)
    cfg = resolve_openai(session, org.id)
    if cfg is None:
        raise AppError(409, "PROVIDER_NOT_CONFIGURED", "Poster chat needs the OpenAI provider (Settings → AI providers)")
    estimate = settings.llm_estimated_usd_per_call
    if committed_this_month(session, org.id) + estimate > org.monthly_budget_usd:
        raise AppError(402, "BUDGET_EXCEEDED", "The organization's monthly budget is exhausted")

    assets = list(session.scalars(select(BrandAsset).where(BrandAsset.organization_id == org.id)
                                  .order_by(BrandAsset.created_at)))
    by_id = {str(a.id): a for a in assets}
    logos = [a.name for a in assets if a.kind != "artwork"]
    artworks = [a.name for a in assets if a.kind == "artwork"]
    d = body.draft
    current = {
        "theme": d.theme, "tag": d.tag, "title": d.title, "edition": d.edition, "date_text": d.date_text,
        "headline": d.headline, "venue": d.venue, "time_text": d.time_text, "art": d.art,
        "artwork": by_id[d.art_asset_id].name if d.art_asset_id in by_id else None,
        "event_logo": by_id[d.event_logo_id].name if d.event_logo_id in by_id else None,
        "sponsor_logos": [by_id[i].name for i in d.sponsor_logo_ids if i in by_id],
        "presenter_label": d.presenter_label,
        "presenter_logos": [by_id[i].name for i in d.presenter_logo_ids if i in by_id],
        "variants": d.variants,
        "template": d.template,
        "game_logo": by_id[d.game_logo_id].name if d.game_logo_id in by_id else None,
        "subtitle": d.subtitle, "prize_label": d.prize_label, "prize_amount": d.prize_amount,
        "registration_text": d.registration_text, "badge_text": d.badge_text, "category_lines": d.category_lines,
        "schedule": d.schedule, "cta_text": d.cta_text, "qr_url": d.qr_url, "venue_detail": d.venue_detail,
        "venue_photo": by_id[d.venue_photo_id].name if d.venue_photo_id in by_id else None,
        "art_zoom": d.art_zoom, "art_x": d.art_x, "art_y": d.art_y,
        "hero_left": by_id[d.hero_left_id].name if d.hero_left_id in by_id else None,
        "hero_right": by_id[d.hero_right_id].name if d.hero_right_id in by_id else None,
    }
    transcript = "\n".join(f"{m.role.upper()}: {m.content}" for m in body.messages[-20:])
    user = (
        f"Interface language: {'Thai' if body.language == 'th' else 'English'}\n"
        f"Logo library (names): {json.dumps(logos, ensure_ascii=False)}\n"
        f"Artwork library (names): {json.dumps(artworks, ensure_ascii=False)}\n"
        f"Current draft:\n{json.dumps(current, ensure_ascii=False)}\n\n"
        f"Conversation so far (latest last):\n{transcript}"
    )
    try:
        out, usage = make_openai(cfg).complete_json(SYSTEM, user, ModelDraft, temperature=0.4)
    except AgentError as exc:
        raise AppError(502, exc.code, f"Assistant error: {exc.message[:300]}") from exc
    session.add(UsageEvent(organization_id=org.id, agent="assistant", provider="openai", operation="chat",
                           units={"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                                  "purpose": "poster_chat"}, cost_usd=usage.cost_usd))
    session.commit()

    # Map names back to ids; anything the model made up is dropped and reported.
    def find(name: str | None, kind: str) -> str | None:
        if not name:
            return None
        key = name.strip().casefold()
        hit = next((a for a in assets if a.name.casefold() == key and (a.kind == "artwork") == (kind == "artwork")), None)
        if hit is None:
            unknown.append(name)
            return None
        return str(hit.id)

    # Fields the model left out keep their current value: a partial or empty answer must never wipe the
    # form. An empty title is also treated as "keep" (a poster always needs one).
    m = {**current, **{k: getattr(out, k) for k in out.model_fields_set if k not in ("reply", "ready")}}
    if not str(m.get("title") or "").strip():
        m["title"] = current["title"]

    def find_any(name: str | None) -> str | None:  # characters may be logos (cut-outs) or artwork
        if not name:
            return None
        key = name.strip().casefold()
        hit = next((a for a in assets if a.name.casefold() == key), None)
        if hit is None:
            unknown.append(name)
            return None
        return str(hit.id)

    unknown: list[str] = []
    art_id = find(m["artwork"], "artwork")
    event_id = find(m["event_logo"], "logo")
    game_id = find(m["game_logo"], "logo")
    photo_id = find(m["venue_photo"], "artwork")
    hero_l = find_any(m["hero_left"])
    hero_r = find_any(m["hero_right"])
    sponsors = [i for i in (find(n, "logo") for n in (m["sponsor_logos"] or [])[:8]) if i and i != event_id]
    presenters = [i for i in (find(n, "logo") for n in (m["presenter_logos"] or [])[:6])
                  if i and i != event_id and i not in sponsors]
    draft = {
        "theme": m["theme"] if m["theme"] in ("gold", "neon", "crimson") else d.theme,
        "tag": _clip(m["tag"], 24), "title": _clip(m["title"], 40), "edition": _clip(m["edition"], 12),
        "date_text": _drop_invented_weekday(_clip(m["date_text"], 60),
                                            " ".join(x.content for x in body.messages if x.role == "user")),
        "headline": [_clip(h, 48) for h in (m["headline"] or []) if str(h).strip()][:3],
        "venue": _clip(m["venue"], 80), "time_text": _clip(m["time_text"], 40),
        # An empty character description never wipes the current one (the AI would paint a generic default).
        "art": "" if art_id else (_clip(m["art"], 400) or d.art), "art_asset_id": art_id,
        "event_logo_id": event_id, "sponsor_logo_ids": list(dict.fromkeys(sponsors)),
        "presenter_label": _clip(m["presenter_label"], 40) or "Presented by",
        "presenter_logo_ids": list(dict.fromkeys(presenters)),
        "variants": 1 if art_id else max(1, min(3, int(m["variants"] or 1))),
        "template": m["template"] if m["template"] in ("esports", "tournament") else d.template,
        "game_logo_id": game_id,
        "subtitle": _clip(m["subtitle"], 90), "prize_label": _clip(m["prize_label"], 40),
        "prize_amount": _clip(m["prize_amount"], 24), "registration_text": _clip(m["registration_text"], 80),
        "badge_text": _clip(m["badge_text"], 40),
        "category_lines": [_clip(x, 60) for x in (m["category_lines"] or []) if str(x).strip()][:2],
        "schedule": [_schedule(x) for x in (m["schedule"] or [])][:2],
        "cta_text": _clip(m["cta_text"], 20),
        "qr_url": _url(m["qr_url"]),
        "venue_detail": _clip(m["venue_detail"], 80), "venue_photo_id": photo_id,
        "art_zoom": _num(m["art_zoom"], 0.5, 2.5, 1.0),
        "art_x": _num(m["art_x"], -0.6, 0.6, 0.0),
        "art_y": _num(m["art_y"], -0.6, 0.6, 0.0),
        "hero_left_id": hero_l, "hero_right_id": hero_r,
    }
    if draft["template"] == "tournament":
        # The tournament layout shows no headline: a who-can-enter line the model put there belongs in the
        # category lines (the model sometimes mixes the two layouts' fields).
        headline: list[str] = list(draft["headline"])  # type: ignore[arg-type]
        category: list[str] = list(draft["category_lines"])  # type: ignore[arg-type]
        spill = [_clip(h, 60) for h in headline if h != draft["subtitle"]]
        if spill and len(category) < 2:
            draft["category_lines"] = list(dict.fromkeys([*spill, *category]))[:2]
    # The draft must be something the poster form accepts; a title is required only to create it.
    if draft["title"]:
        try:
            schemas.PosterSpec.model_validate(draft)
        except ValidationError as exc:
            raise AppError(502, "LLM_INVALID_OUTPUT", f"The assistant returned an unusable draft: {exc.errors()[:2]}") from exc
    changed = draft != {**d.model_dump(), "headline": [h for h in d.headline if h.strip()]}
    if not (reply := _clip(out.reply, 800)):
        if draft["art"] and draft["art"] != d.art:
            reply = ("ใส่ตัวละครให้แล้วค่ะ AI จะวาดภาพตัวละครจริงตอนกดสร้างโปสเตอร์ ตอนนี้ตัวอย่างเป็นภาพแทนนะคะ"
                     if body.language == "th" else
                     "Character added. The AI paints it when you press Create poster; the preview shows a stand-in.")
        elif changed:
            reply = "อัปเดตโปสเตอร์แล้วค่ะ" if body.language == "th" else "Poster updated."
        else:
            reply = ("ขอโทษค่ะ ไม่ได้เปลี่ยนอะไร ช่วยบอกอีกครั้งได้ไหมคะ" if body.language == "th"
                     else "Sorry, nothing changed. Could you say that again?")
    return PosterChatResponse(reply=reply, draft=draft, ready=bool(out.ready and draft["title"]),
                              unknown_assets=unknown)
