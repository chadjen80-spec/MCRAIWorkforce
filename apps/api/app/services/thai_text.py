"""Thai-aware text drawing for Pillow.

With libraqm (bundled in Pillow's Linux wheels, so present in the Docker image) HarfBuzz
positions stacked marks correctly. Without it (typical on Windows) a tone mark that follows
an upper vowel is drawn at the same height and disappears into it, so we lift it manually.
"""
from __future__ import annotations

from functools import lru_cache

from PIL import ImageDraw, ImageFont, features

UPPER_VOWELS = set("ัิีึื็ํ")
TONE_MARKS = set("่้๊๋์")


@lru_cache
def has_raqm() -> bool:
    return bool(features.check("raqm"))


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill) -> None:
    """Draw a single line, left/top anchored."""
    if has_raqm() or not any(ch in TONE_MARKS for ch in text):
        draw.text(xy, text, font=font, fill=fill)
        return
    x, y = xy
    base: list[str] = []
    lifted: list[tuple[str, str]] = []  # (mark, text before it)
    for i, ch in enumerate(text):
        if ch in TONE_MARKS and i > 0 and text[i - 1] in UPPER_VOWELS:
            lifted.append((ch, "".join(base)))
        else:
            base.append(ch)
    draw.text((x, y), "".join(base), font=font, fill=fill)
    for mark, prefix in lifted:
        draw.text((x + font.getlength(prefix), y - font.size * 0.2), mark, font=font, fill=fill)


def lift_marks_markup(escaped_text: str, font_size: float) -> str:
    """ReportLab paragraph markup that raises tone marks stacked on an upper vowel.

    ReportLab does not apply OpenType mark-to-mark positioning, so without this the tone mark
    is drawn on top of the vowel and vanishes (e.g. "เมื่อ" would print as "เมือ").
    """
    out: list[str] = []
    for i, ch in enumerate(escaped_text):
        if ch in TONE_MARKS and i > 0 and escaped_text[i - 1] in UPPER_VOWELS:
            out.append(f'<super rise="{font_size * 0.2:.1f}" size="{font_size}">{ch}</super>')
        else:
            out.append(ch)
    return "".join(out)
