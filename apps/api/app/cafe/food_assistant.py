"""Mira's food & drink ordering: find menu items and quantities in what was said, then price them from the menu.

"สั่งมาม่าต้มยำ 1 ที่ กับโค้ก 2 ขวด" -> [(มาม่าต้มยำ, 1), (โค้ก, 2)]. Names are matched against the café's own menu (names and
other spoken names); quantities are digits or Thai/English number words next to the name. Nothing here invents an item
or a price: an item that is not on the menu is reported as such, and prices come from `orders.price_lines`.
"""
from __future__ import annotations

import re

from app.cafe.models import CafeMenuItem

ORDER_VERB = re.compile(r"สั่ง|ขอ|เอา|อยากได้|อยากกิน|อยากดื่ม|\b(order|get me|i'?d like|i want|can i have)\b", re.I)
NUMBER_WORDS = {
    "หนึ่ง": 1, "นึง": 1, "สอง": 2, "สาม": 3, "สี่": 4, "ห้า": 5, "หก": 6, "เจ็ด": 7, "แปด": 8, "เก้า": 9, "สิบ": 10,
    "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
UNITS = r"(?:ที่|แก้ว|จาน|ชาม|ขวด|กระป๋อง|ชิ้น|อัน|ห่อ|ถ้วย|กล่อง|ไม้|cups?|glass(?:es)?|bottles?|cans?|plates?|bowls?|pieces?|x)?"
# English number words only as whole words ("an" is not the start of "and"); digits not inside a longer number
_NUM = r"((?<!\d)\d{1,2}(?!\d)|(?<![a-z])(?:" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")(?![a-z]))"
AFTER = re.compile(r"^\s*(?:x\s*)?" + _NUM + r"\s*" + UNITS, re.I)
BEFORE = re.compile(_NUM + r"\s*(?:x\s*)?" + UNITS + r"\s*$", re.I)


def _num(word: str) -> int:
    return int(word) if word.isdigit() else NUMBER_WORDS[word.lower()]


def _names(item: CafeMenuItem) -> list[str]:
    return [n for n in [item.name, *(item.aliases or [])] if len(n.strip()) >= 2]


def match_lines(text: str, items: list[CafeMenuItem]) -> list[tuple[CafeMenuItem, int]]:
    """Menu items named in the text with their quantities (default 1), longest names first, without overlaps."""
    low = text.lower()
    taken = [False] * len(low)
    found: list[tuple[int, CafeMenuItem, int]] = []
    candidates = sorted(((n.lower().strip(), i) for i in items for n in _names(i)), key=lambda x: len(x[0]), reverse=True)
    for name, item in candidates:
        start = 0
        while (pos := low.find(name, start)) >= 0:
            end = pos + len(name)
            start = end
            if any(taken[pos:end]) or any(f[1].id == item.id for f in found):
                continue
            # English names must be whole words ("tea" is not part of "steam"); a plural "s"/"es" is fine ("2 cokes")
            if name.isascii():
                plural = re.match(r"(?:es|s)?", low[end:]).end()  # type: ignore[union-attr]
                if (pos > 0 and low[pos - 1].isalnum()) or (end + plural < len(low) and low[end + plural].isalnum()):
                    continue
                end += plural
            for k in range(pos, end):
                taken[k] = True
            qty = 1
            if m := AFTER.match(low[end:]):
                qty = _num(m.group(1))
                for k in range(end, end + m.end()):
                    taken[k] = True
            elif m := BEFORE.search(low[max(0, pos - 12):pos]):
                qty = _num(m.group(1))
            found.append((pos, item, max(1, min(qty, 20))))
    return [(item, qty) for _, item, qty in sorted(found, key=lambda f: f[0])]


def resolve_named(items_arg: object, menu_items: list[CafeMenuItem]) -> tuple[list[tuple[CafeMenuItem, int]], list[str]]:
    """For the model's structured call: [{"name": ..., "quantity": ...}] -> menu lines, plus names not on the menu."""
    lines: list[tuple[CafeMenuItem, int]] = []
    unknown: list[str] = []
    if not isinstance(items_arg, list):
        return lines, unknown
    for entry in items_arg[:30]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        qty = entry.get("quantity", 1)
        qty = qty if isinstance(qty, int) and not isinstance(qty, bool) else 1
        hit = match_lines(name, menu_items)
        if hit:
            lines.append((hit[0][0], max(1, min(qty, 20))))
        elif name:
            unknown.append(name[:64])
    return lines, unknown


def baht(amount: str) -> str:
    """"1234.50" -> "1,234.50", "45.00" -> "45"."""
    whole, _, frac = amount.partition(".")
    text = f"{int(whole):,}"
    return text if frac in ("", "00") else f"{text}.{frac}"
