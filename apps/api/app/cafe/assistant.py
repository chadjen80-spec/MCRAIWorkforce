"""/v1/cafe/assistant: Mira's café tools (structured tool calling, replies grounded in real results).

1. Plan: the text becomes ONE structured tool call {name, arguments}. With `use_ai` and an OpenAI key, the model picks
   it through function calling (it can only choose a declared tool and its arguments); otherwise, or when the model's
   answer is unusable, Thai/English rules produce the same structure.
2. Ground: arguments are resolved against the database (spoken app name -> catalogue key, seat label -> seat).
3. Confirm: tools with side effects come back with `confirm` and run only through /assistant/confirm.
4. Reply: every sentence is a fixed template filled from the result object. The model never writes the facts, so Mira
   cannot claim a launch, an order, a payment, a price, availability or a PC's status that did not happen.

Text that is not a café command returns intent=null and Mira's other flows run unchanged.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cafe import flags, service
from app.cafe.api import staff
from app.cafe.models import CafeApp, CafeCommand, CafeSeat
from app.config import get_settings
from app.db import get_db
from app.deps import Principal
from app.providers.config import resolve_openai
from app.services import router as hybrid_router

router = APIRouter(prefix="/v1/cafe", tags=["smart-cafe"])

ToolName = Literal["launch_app", "get_remaining_time", "request_staff", "order_food", "top_up_time", "print_document"]

# Function schemas offered to the model. Arguments are names as the customer said them; the server resolves them.
TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "launch_app", "description": "Open an approved game or program on a seat PC.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["app"],
                       "properties": {"app": {"type": "string", "description": "Game or program name as said"},
                                      "seat": {"type": "string", "description": "Seat number, if said"}}}}},
    {"type": "function", "function": {
        "name": "get_remaining_time", "description": "How much play time is left on a seat.",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {"seat": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "request_staff", "description": "Call a staff member to a seat.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {"seat": {"type": "string"}, "message": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "order_food", "description": "Order food or drinks to a seat.",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "seat": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "quantity": {"type": "integer"}}}}}}}},
    {"type": "function", "function": {
        "name": "top_up_time", "description": "Buy more play time for a seat.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {"seat": {"type": "string"}, "minutes": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "print_document", "description": "Print a document from a seat.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {"seat": {"type": "string"}, "copies": {"type": "integer"}}}}},
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
MODULE_OF: dict[str, str] = {"launch_app": "launcher", "get_remaining_time": "sessions", "request_staff": "assist",
                             "order_food": "orders", "top_up_time": "pos", "print_document": "printing"}


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source: Literal["rules", "model"] = "rules"


# ---- rules (no AI, no cost) -----------------------------------------------------------------------------------
SEAT_RE = re.compile(r"(?:เครื่อง(?:ที่)?|ที่นั่ง(?:ที่)?|โต๊ะ(?:ที่)?|\bseat|\bpc)\s*(?:no\.?|#|number)?\s*([A-Za-z]?\d{1,4}[A-Za-z]?)\b", re.I)
LAUNCH_VERB = re.compile(r"เปิด|เล่น|รัน|เข้าเกม|\b(launch|open|start|play|run)\b", re.I)
GAME_WORD = re.compile(r"เกม|\bgame\b|\bplay\b|เล่น", re.I)
# "เปิดหน้าร้านเกม" / "open the games page" ask for a page, not a game: Mira opens café pages on the web side.
PAGE_WORD = re.compile(r"หน้า|เมนู|ร้านเกม|\b(page|screen|menu|tab)\b", re.I)
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("request_staff", re.compile(r"เรียก(พนักงาน|แอดมิน|น้อง|staff)|ขอความช่วยเหลือ|ช่วยด้วย|call (a |the )?(staff|admin)|need (some )?help", re.I)),
    ("get_remaining_time", re.compile(r"เหลือเวลา|เวลา(ที่)?เหลือ|เหลือ(อีก)?กี่(นาที|ชั่วโมง|ชม)|time (left|remaining)|remaining time|how much time", re.I)),
    ("top_up_time", re.compile(r"เติมเวลา|ซื้อเวลา|ต่อเวลา|top ?up|add (more )?time|buy (more )?time", re.I)),
    ("order_food", re.compile(r"สั่ง(อาหาร|ข้าว|มาม่า|น้ำ|เครื่องดื่ม|กาแฟ|ขนม)|หิวข้าว|order (some )?(food|drinks?|a drink|noodles)", re.I)),
    ("print_document", re.compile(r"ปริ้น|ปรินท์|พิมพ์(เอกสาร|ไฟล์|งาน)|\bprint (this|my|a|the)? ?(file|document|page)", re.I)),
]


def _norm(text: str) -> str:
    return re.sub(r"[\s\-_.:]+", "", text.lower())


def _within_one_edit(a: str, b: str) -> bool:
    """True when a and b differ by at most one inserted, deleted or changed character."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return edits + (len(b) - j) <= 1


def _fuzzy_in(name: str, text: str) -> bool:
    """`name` appears in `text` with at most one wrong character, e.g. speech heard "สตรีม" for "สตีม" (Steam)."""
    if len(name) < 4:  # short names must match exactly, or almost anything would match
        return False
    return any(_within_one_edit(name, text[i:i + size]) for size in (len(name) - 1, len(name), len(name) + 1)
               for i in range(0, max(0, len(text) - size) + 1))


def match_app(text: str, apps: list[CafeApp]) -> CafeApp | None:
    """The catalogue app whose name or alias appears in the text (longest name wins); then a one-letter slip is allowed.
    A near match is safe here because a launch always needs a yes ("Open Steam on seat 12?")."""
    t = _norm(text)
    names = [(_norm(n), a) for a in apps for n in [a.name, a.app_key, *(a.aliases or [])]]
    exact = [(len(n), a) for n, a in names if len(n) >= 2 and n in t]
    if exact:
        return max(exact, key=lambda x: x[0])[1]
    near = [(len(n), a) for n, a in names if _fuzzy_in(n, t)]
    return max(near, key=lambda x: x[0])[1] if near else None


def plan_rules(text: str, apps: list[CafeApp]) -> ToolCall | None:
    seat = SEAT_RE.search(text)
    args: dict[str, Any] = {"seat": seat.group(1)} if seat else {}
    for name, pattern in RULES:
        if pattern.search(text):
            return ToolCall(name, args)
    if LAUNCH_VERB.search(text):
        app = match_app(text, apps)
        if app is not None:
            return ToolCall("launch_app", {**args, "app": app.name})
        # "เปิดเกม X" or "เปิด X ที่เครื่อง 12" with an X we do not have: say so instead of guessing (rather than letting
        # it become a creative job). A page request is not a launch.
        if (GAME_WORD.search(text) or seat) and not PAGE_WORD.search(text):
            spoken = SEAT_RE.sub("", LAUNCH_VERB.sub("", text))
            spoken = re.sub(r"เกม|โปรแกรม|ให้หน่อย|หน่อย|ให้|อยาก|ขอ|ครับ|ค่ะ|คะ|นะ|ที่|please|\bgame\b|\bthe\b", "", spoken, flags=re.I)
            spoken = re.sub(r"\s+", " ", spoken).strip(" ,.!?")
            return ToolCall("launch_app", {**args, "app": spoken[:64]})
    return None


# ---- structured tool calling with a model ------------------------------------------------------------------------
SYSTEM = (
    "You are Mira, the assistant of an internet café. Decide whether the customer's message asks for one of the tools. "
    "If it does, call exactly one tool with arguments taken from the message (names as the customer said them). "
    "If it does not, answer with the single word NONE. Never invent a seat number, a price or an app name."
)


def plan_model(session: Session, org_id: uuid.UUID, text: str) -> ToolCall | None | Literal["unavailable"]:
    cfg = resolve_openai(session, org_id)
    if cfg is None:
        return "unavailable"
    import httpx

    try:
        with httpx.Client(base_url=cfg.base_url, headers={"Authorization": f"Bearer {cfg.api_key}"}, timeout=20,
                          transport=hybrid_router.TRANSPORTS.get("openai")) as http:
            resp = http.post("/chat/completions", json={
                "model": cfg.model, "temperature": 0, "tools": TOOLS, "tool_choice": "auto",
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text[:2000]}]})
        if resp.status_code != 200:
            return "unavailable"
        message = resp.json()["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            return None
        fn = calls[0]["function"]
        name = fn["name"]
        args = json.loads(fn.get("arguments") or "{}")
        if name not in TOOL_NAMES or not isinstance(args, dict):
            return "unavailable"
        allowed = set(next(t for t in TOOLS if t["function"]["name"] == name)["function"]["parameters"]["properties"])
        return ToolCall(name, {k: v for k, v in args.items() if k in allowed}, source="model")
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError, StopIteration):
        return "unavailable"


# ---- replies (templates filled from results; Thai first) -----------------------------------------------------------
def _t(lang: str, th: str, en: str) -> str:
    return th if lang == "th" else en


def _duration(seconds: int, lang: str) -> str:
    h, m = divmod(max(0, seconds) // 60, 60)
    if lang == "th":
        return (f"{h} ชั่วโมง " if h else "") + f"{m} นาที"
    return (f"{h} h " if h else "") + f"{m} min"


ERROR_TEXT: dict[str, tuple[str, str]] = {
    "APP_NOT_IN_CATALOG": ("ร้านยังไม่มี \"{app}\" ในรายการเกมที่อนุญาตค่ะ", "\"{app}\" is not in the café's approved list."),
    "APP_DISABLED": ("{app} ถูกปิดใช้งานในร้านอยู่ค่ะ", "{app} is disabled in this café."),
    "NO_DEVICE": ("ที่นั่ง {seat} ยังไม่ได้ลงทะเบียนเครื่องค่ะ", "Seat {seat} has no registered PC."),
    "DEVICE_OFFLINE": ("เครื่องที่นั่ง {seat} ออฟไลน์อยู่ค่ะ ยังสั่งเปิดไม่ได้", "The PC at seat {seat} is offline, so I can't open anything on it."),
    "APP_NOT_INSTALLED": ("เครื่องที่นั่ง {seat} ยังไม่ได้ติดตั้ง {app} ค่ะ", "{app} is not installed on seat {seat}."),
    "NO_ACTIVE_SESSION": ("ที่นั่ง {seat} ยังไม่มีเวลาเล่นค่ะ ต้องเปิดรอบเล่นก่อน", "Seat {seat} has no play time; start a session first."),
    "SEAT_UNAVAILABLE": ("ที่นั่ง {seat} ปิดให้บริการอยู่ค่ะ", "Seat {seat} is out of service."),
    "SEAT_NOT_FOUND": ("ไม่พบที่นั่ง {seat} ค่ะ", "I can't find seat {seat}."),
}
RESULT_TEXT: dict[str, tuple[str, str]] = {
    "STARTED": ("เปิด {app} ที่ที่นั่ง {seat} แล้วค่ะ", "{app} is now open on seat {seat}."),
    "NOT_ALLOWLISTED": ("เครื่องที่นั่ง {seat} ปฏิเสธคำสั่ง เพราะ {app} ไม่อยู่ในรายการที่เครื่องอนุญาตค่ะ",
                        "The PC at seat {seat} refused: {app} is not on its local allowlist."),
    "NOT_INSTALLED": ("เครื่องที่นั่ง {seat} หาไฟล์ {app} ไม่พบค่ะ", "The PC at seat {seat} could not find {app}'s program file."),
    "START_FAILED": ("เครื่องที่นั่ง {seat} เปิด {app} ไม่สำเร็จค่ะ ({message})", "The PC at seat {seat} could not start {app} ({message})."),
    "EXPIRED": ("คำสั่งเปิด {app} หมดเวลาก่อนเครื่องที่นั่ง {seat} ตอบกลับค่ะ", "The launch of {app} expired before seat {seat} answered."),
}


def error_reply(code: str, lang: str, **values: Any) -> str:
    th, en = ERROR_TEXT.get(code, ("ทำรายการไม่สำเร็จค่ะ ({code})", "That didn't work ({code})."))
    return _t(lang, th, en).format(code=code, **{k: values.get(k, "") for k in ("app", "seat")})


def result_reply(cmd: CafeCommand, app_name: str, seat_label: str, lang: str) -> str:
    if cmd.status in ("queued", "delivered"):
        return _t(lang, f"ส่งคำสั่งเปิด {app_name} ไปที่ที่นั่ง {seat_label} แล้ว กำลังรอเครื่องตอบกลับค่ะ",
                  f"I sent the launch of {app_name} to seat {seat_label}; still waiting for the PC to answer.")
    code = (cmd.result or {}).get("code", "START_FAILED")
    th, en = RESULT_TEXT.get(code, RESULT_TEXT["START_FAILED"])
    return _t(lang, th, en).format(app=app_name, seat=seat_label, message=(cmd.result or {}).get("message", "") or code)


# ---- endpoints -------------------------------------------------------------------------------------------------------
class AssistantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    language: Literal["th", "en"] = "th"
    use_ai: bool = False


class AssistantOut(BaseModel):
    intent: str | None
    tool_call: dict[str, Any] | None = None
    reply: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    confirm: dict[str, Any] | None = None


def _resolve_seat(session: Session, org_id: uuid.UUID, label: str | None) -> CafeSeat | None:
    if label:
        return session.scalar(select(CafeSeat).where(CafeSeat.organization_id == org_id, CafeSeat.label == str(label).strip()))
    seats = session.scalars(select(CafeSeat).where(CafeSeat.organization_id == org_id).limit(2)).all()
    return seats[0] if len(seats) == 1 else None  # a one-seat shop needs no seat number; otherwise ask


@router.post("/assistant", response_model=AssistantOut)
def assistant(body: AssistantIn, principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    if not flags.enabled("assistant"):
        return AssistantOut(intent=None)  # café off: Mira's other flows handle the text
    org_id, lang = principal.organization.id, body.language
    apps = list(session.scalars(select(CafeApp).where(CafeApp.organization_id == org_id)))
    call: ToolCall | None = None
    if body.use_ai:
        planned = plan_model(session, org_id, body.text)
        call = planned if isinstance(planned, ToolCall) else (None if planned is None else plan_rules(body.text, apps))
    else:
        call = plan_rules(body.text, apps)
    if call is None:
        return AssistantOut(intent=None)
    tool = {"name": call.name, "arguments": call.arguments, "source": call.source}
    module = MODULE_OF[call.name]
    if not flags.enabled(module):
        return AssistantOut(intent=call.name, tool_call=tool, data={"module": module, "enabled": False}, reply=_t(
            lang, "ฟีเจอร์นี้ยังไม่เปิดใช้งานในร้านค่ะ กรุณาติดต่อพนักงานที่เคาน์เตอร์",
            "That feature isn't enabled in this café yet. Please ask at the counter."))
    seat_label = call.arguments.get("seat")
    seat = _resolve_seat(session, org_id, seat_label)
    if seat is None:
        if seat_label:
            return AssistantOut(intent=call.name, tool_call=tool, reply=error_reply("SEAT_NOT_FOUND", lang, seat=seat_label),
                                data={"error": "SEAT_NOT_FOUND"})
        return AssistantOut(intent=call.name, tool_call=tool, data={"needs": "seat"},
                            reply=_t(lang, "ที่นั่งหมายเลขอะไรคะ", "Which seat number?"))
    if call.name == "get_remaining_time":
        cs = service.active_session(session, seat)
        left = service.remaining_seconds(cs)
        reply = (_t(lang, f"ที่นั่ง {seat.label} เหลือเวลา {_duration(left, lang)}ค่ะ", f"Seat {seat.label} has {_duration(left, lang)} left.")
                 if left > 0 else _t(lang, f"ที่นั่ง {seat.label} ไม่มีเวลาเล่นเหลืออยู่ค่ะ", f"Seat {seat.label} has no play time left."))
        return AssistantOut(intent=call.name, tool_call=tool, reply=reply,
                            data={"seat_label": seat.label, "remaining_seconds": left, "session_active": left > 0})
    if call.name == "request_staff":
        return AssistantOut(intent=call.name, tool_call=tool, data={"seat_label": seat.label},
                            reply=_t(lang, f"เรียกพนักงานไปที่ที่นั่ง {seat.label} ใช่ไหมคะ", f"Call a staff member to seat {seat.label}?"),
                            confirm={"action": "request_staff",
                                     "payload": {"seat_id": str(seat.id), "message": str(call.arguments.get("message", ""))[:200]}})
    if call.name == "launch_app":
        spoken = str(call.arguments.get("app", "")).strip()
        app = match_app(spoken, apps) if spoken else None
        if app is None:
            return AssistantOut(intent=call.name, tool_call=tool, reply=error_reply("APP_NOT_IN_CATALOG", lang, app=spoken or "?"),
                                data={"error": "APP_NOT_IN_CATALOG", "app": spoken})
        try:  # check now so Mira doesn't ask "shall I?" for something that cannot happen
            service.authorize_launch(session, seat, app.app_key)
        except service.CafeError as exc:
            return AssistantOut(intent=call.name, tool_call=tool, reply=error_reply(exc.code, lang, app=app.name, seat=seat.label),
                                data={"error": exc.code, "seat_label": seat.label, "app": app.name})
        return AssistantOut(intent=call.name, tool_call=tool, data={"seat_label": seat.label, "app": app.name, "app_key": app.app_key},
                            reply=_t(lang, f"เปิด {app.name} ที่ที่นั่ง {seat.label} ใช่ไหมคะ", f"Open {app.name} on seat {seat.label}?"),
                            confirm={"action": "launch_app", "payload": {"seat_id": str(seat.id), "app_key": app.app_key}})
    # order_food / top_up_time / print_document: their modules have no implementation yet even when listed
    return AssistantOut(intent=call.name, tool_call=tool, data={"module": module, "implemented": False}, reply=_t(
        lang, "ฟีเจอร์นี้ยังไม่พร้อมใช้งานค่ะ กรุณาติดต่อพนักงานที่เคาน์เตอร์", "That feature isn't available yet. Please ask at the counter."))


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["launch_app", "request_staff"]
    payload: dict[str, Any]
    language: Literal["th", "en"] = "th"
    idempotency_key: str | None = Field(default=None, max_length=128)


@router.post("/assistant/confirm", response_model=AssistantOut)
def assistant_confirm(body: ConfirmIn, principal: Principal = Depends(staff), session: Session = Depends(get_db),
                      idempotency_key: str | None = Header(default=None, max_length=128)):
    """A person said yes. Runs the action through the same checks as the buttons and reports the real outcome."""
    flags.require("assistant")
    lang, org_id = body.language, principal.organization.id
    try:
        seat = service.find_seat(session, org_id, seat_id=uuid.UUID(str(body.payload.get("seat_id"))))
    except (ValueError, service.CafeError):
        return AssistantOut(intent=body.action, reply=error_reply("SEAT_NOT_FOUND", lang, seat="?"), data={"error": "SEAT_NOT_FOUND"})
    if body.action == "request_staff":
        flags.require("assist")
        req = service.request_assist(session, seat, service.seat_device(session, seat), str(body.payload.get("message", ""))[:200],
                                     source="mira", actor=principal.actor)
        return AssistantOut(intent="request_staff", data={"assist_id": str(req.id), "seat_label": seat.label, "status": req.status},
                            reply=_t(lang, f"แจ้งพนักงานแล้วค่ะ จะมีคนไปที่ที่นั่ง {seat.label}", f"Staff have been notified for seat {seat.label}."))
    flags.require("launcher")
    app_key = str(body.payload.get("app_key", ""))
    app = service.find_app(session, org_id, app_key)
    app_name = app.name if app else app_key
    try:
        cmd = service.launch(session, seat, app_key, actor=principal.actor, via="mira", idempotency_key=body.idempotency_key or idempotency_key)
    except service.CafeError as exc:
        return AssistantOut(intent="launch_app", reply=error_reply(exc.code, lang, app=app_name, seat=seat.label),
                            data={"error": exc.code, "seat_label": seat.label, "app": app_name})
    deadline = time.monotonic() + get_settings().cafe_launch_wait_seconds
    while cmd.status in ("queued", "delivered") and time.monotonic() < deadline:
        time.sleep(0.2)
        session.expire_all()
        cmd = session.get(CafeCommand, cmd.id) or cmd
    return AssistantOut(intent="launch_app", reply=result_reply(cmd, app_name, seat.label, lang),
                        data={"command_id": str(cmd.id), "status": cmd.status, "result": cmd.result, "seat_label": seat.label,
                              "app": app_name, "link": "/cafe/devices"})
