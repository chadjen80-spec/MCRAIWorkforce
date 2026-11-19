"""Tournament poster (4:5, 1600x2000): the layout of a provincial esports series poster.

Top: key art (two rivals around an energy orb) with the event logo top-left, the organisers' logos
top-right and the game's logo in the middle. Below, on dark navy: project name, prize line, a huge gold
prize amount, registration deadline, a red "free entry" pill beside the category, a schedule card, a QR
code to register, the venue with a photo, and a black sponsor strip. As with the esports template the
AI paints only the artwork; every word, logo and the QR code is drawn here, exactly.
"""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from app.agents.poster import _fit_logo, _logo_row, _trim, _vgradient
from app.services.media import find_kanit
from app.services.thai_text import draw_text

TW, TH = 1600, 2000
ART_H = 1067  # the artwork is laid out 3:2 at the top and fades into the navy below
NAVY_TOP, NAVY_BOTTOM = (10, 14, 52), (6, 8, 30)
GOLD = ((255, 250, 226), (238, 196, 112))
RED = ((214, 36, 46), (120, 10, 20))
YELLOW = (255, 226, 70)

DEFAULT_ART = "two original rival esports fighters facing off, intense expressions, upper-body portraits"


def build_prompt(poster: dict[str, Any], art: str | None) -> str:
    return (
        f"Esports tournament key art, wide banner composition, game splash-art quality. {art or DEFAULT_ART}. "
        "Original characters, not based on any existing game, franchise or real person. "
        "Two subjects on the left and right thirds, facing the centre, heads in the upper half: the left side lit "
        "by electric blue light over a blue starfield, the right side lit by red light over red energy streaks. "
        "A glowing white-blue energy orb with lightning in the centre, behind where a logo will be placed. "
        "Highly detailed digital painting, cinematic rim light, sharp focus. "
        "No text, no letters, no logos, no watermarks."
    )


# --- drawing helpers -----------------------------------------------------------------------
def _font(weight: str, size: int, italic: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_kanit(weight, italic), size)


def _fit(text: str, weight: str, size: int, max_w: int, italic: bool = False, min_size: int = 24) -> ImageFont.FreeTypeFont:
    while size > min_size:
        font = _font(weight, size, italic)
        if font.getlength(text) <= max_w:
            return font
        size -= 4
    return _font(weight, min_size, italic)


def _line(text: str, font: ImageFont.FreeTypeFont, highlight: tuple[int, int, int] | None = None,
          base: tuple[int, int, int] = (255, 255, 255)) -> tuple[Image.Image, Image.Image]:
    """(mask, face) for one line, both cropped to the ink. Segments wrapped in *asterisks* get `highlight`."""
    parts = text.split("*")
    plain = "".join(parts)
    ascent, descent = font.getmetrics()
    pad = int(font.size * 0.45)
    width = int(font.getlength(plain)) + 2 * pad
    mask = Image.new("L", (max(width, 1), ascent + descent + 2 * pad), 0)
    draw_text(ImageDraw.Draw(mask), (pad, pad), plain, font, 255)
    face = Image.new("RGB", mask.size, base)
    if highlight is not None:
        x: float = pad
        for i, part in enumerate(parts):
            w = font.getlength(part)
            if i % 2 == 1 and part:
                face.paste(highlight, (int(x), 0, int(x + w + font.size * 0.05), mask.height))
            x += w
    box = mask.getbbox()
    return (mask.crop(box), face.crop(box)) if box else (mask, face)


def _stamp(canvas: Image.Image, mask: Image.Image, x: int, y: int, face: Any, *,
           outline: tuple[int, int, int] | None = None, outline_px: int = 0,
           glow: tuple[int, int, int] | None = None, glow_px: int = 0, glow_alpha: float = 0.8) -> None:
    """Composite a text/shape mask at (x, y) (top-left of the ink) with a fill colour, gradient or image."""
    pad = max(outline_px, glow_px * 2, 1)
    m = Image.new("L", (mask.width + 2 * pad, mask.height + 2 * pad), 0)
    m.paste(mask, (pad, pad))
    px, py = x - pad, y - pad
    if glow and glow_px:
        halo = m.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(glow_px))
        canvas.paste(Image.new("RGB", m.size, glow), (px, py), halo.point(lambda v: int(v * glow_alpha)))
    if outline and outline_px:
        canvas.paste(Image.new("RGB", m.size, outline), (px, py), m.filter(ImageFilter.MaxFilter(2 * outline_px + 1)))
    if isinstance(face, Image.Image):
        f = Image.new("RGB", m.size)
        f.paste(face, (pad, pad))
    elif isinstance(face, tuple) and isinstance(face[0], tuple):
        f = _vgradient(m.size, *face)
    else:
        f = Image.new("RGB", m.size, face)
    canvas.paste(f, (px, py), m)


def _text(canvas: Image.Image, text: str, font: ImageFont.FreeTypeFont, *, cx: float | None = None,
          x: int | None = None, top: int, fill: Any = (255, 255, 255), highlight: tuple[int, int, int] | None = None,
          **effects: Any) -> int:
    """Draw one line centred on cx (or left-aligned at x) with its ink top at `top`. Returns its ink height."""
    if not text:
        return 0
    mask, face = _line(text, font, highlight)
    left = int(cx - mask.width / 2) if cx is not None else int(x or 0)
    _stamp(canvas, mask, left, top, face if highlight else fill, **effects)
    return mask.height


def _rounded(size: tuple[int, int], radius: int, corners: tuple[bool, bool, bool, bool] = (True,) * 4) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255, corners=corners)
    return m


def _qr(url: str, size: int) -> Image.Image:
    import segno

    matrix = segno.make(url, error="m").matrix
    n = len(matrix)
    small = Image.new("L", (n, n), 255)
    small.putdata([0 if v else 255 for row in matrix for v in row])
    return small.resize((size, size), Image.Resampling.NEAREST)


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * scale + 0.5), int(img.height * scale + 0.5)), Image.Resampling.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


# --- layout ------------------------------------------------------------------------------------
def _background(art: Image.Image, placement: tuple[float, float, float] = (1.0, 0.0, 0.0)) -> Image.Image:
    from app.agents.poster import frame_art

    canvas = _vgradient((TW, TH), NAVY_TOP, NAVY_BOTTOM)
    top = frame_art(art, TW, ART_H, *placement)
    fade = Image.new("L", (1, ART_H))
    for y in range(ART_H):
        fade.putpixel((0, y), 255 if y < 560 else int(255 * max(0.0, 1 - (y - 560) / 380) ** 1.3))
    canvas.paste(top, (0, 0), fade.resize((TW, ART_H)))
    # A soft dark band at the very top so the logos read on any artwork.
    shade = Image.new("L", (1, 260))
    for y in range(260):
        shade.putpixel((0, y), int(120 * (1 - y / 260) ** 1.5))
    canvas.paste((0, 0, 10), (0, 0, TW, 260), shade.resize((TW, 260)))
    return canvas


def space_art() -> Image.Image:
    """The series' key-art backdrop without characters: a blue starfield on the left, red energy on the
    right, a bright orb with lightning in the middle. Used when the user supplies the characters."""
    import math
    import random

    rng = random.Random(7)
    w, h = TW, ART_H
    art = Image.new("RGB", (w, h))
    px = art.load()
    assert px is not None
    for x in range(0, w, 2):
        t = x / (w - 1)
        blend = max(0.0, min(1.0, (t - 0.38) / 0.24))  # blue -> red across the middle
        for y in range(0, h, 2):
            v = 1 - y / h * 0.55
            blue = (int(18 * v), int(42 * v), int(150 * v))
            red = (int(150 * v), int(14 * v), int(26 * v))
            c = (int(blue[0] + (red[0] - blue[0]) * blend), int(blue[1] + (red[1] - blue[1]) * blend),
                 int(blue[2] + (red[2] - blue[2]) * blend))
            for dx in (0, 1):
                for dy in (0, 1):
                    if x + dx < w and y + dy < h:
                        px[x + dx, y + dy] = c
    d = ImageDraw.Draw(art)
    # streaks: blue diagonals on the left, red sweeps on the right
    for _ in range(90):
        x = rng.randint(-200, w)
        y = rng.randint(0, h)
        length = rng.randint(80, 360)
        left = x < w * 0.5
        col = (90, 150, 255) if left else (255, 70, 60)
        d.line([(x, y), (x + length, y - length * (0.35 if left else -0.25))], fill=col, width=rng.choice((1, 1, 2)))
    art = art.filter(ImageFilter.GaussianBlur(1.2))
    d = ImageDraw.Draw(art)
    for _ in range(420):  # stars
        x, y = rng.randint(0, w - 1), rng.randint(0, h - 1)
        lum = rng.randint(120, 255)
        d.point((x, y), fill=(lum, lum, 255) if x < w / 2 else (255, lum, lum))
    # the orb
    cx, cy, r = w // 2, int(h * 0.36), 250
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    art.paste((205, 225, 255), (0, 0), glow.filter(ImageFilter.GaussianBlur(70)).point(lambda v: int(v * 0.85)))
    core = Image.new("L", (w, h), 0)
    ImageDraw.Draw(core).ellipse((cx - r * 0.55, cy - r * 0.55, cx + r * 0.55, cy + r * 0.55), fill=255)
    art.paste((250, 252, 255), (0, 0), core.filter(ImageFilter.GaussianBlur(40)))
    bolts = Image.new("L", (w, h), 0)
    bd = ImageDraw.Draw(bolts)
    for _ in range(9):  # lightning crackling around the orb
        ang = rng.uniform(0, 6.283)
        pts = []
        rad = r * rng.uniform(0.7, 1.15)
        for step in range(7):
            turn = ang + step * 0.12
            rr = rad + rng.uniform(-25, 25)
            pts.append((cx + math.cos(turn) * rr, cy + math.sin(turn) * rr))
        bd.line(pts, fill=255, width=2)
    art.paste((150, 200, 255), (0, 0), bolts.filter(ImageFilter.GaussianBlur(7)))
    art.paste((235, 245, 255), (0, 0), bolts.filter(ImageFilter.GaussianBlur(1.2)).point(lambda v: int(v * 0.7)))
    # the sphere: a bright core with a cool rim
    sphere = Image.new("L", (w, h), 0)
    ImageDraw.Draw(sphere).ellipse((cx - r * 0.8, cy - r * 0.8, cx + r * 0.8, cy + r * 0.8), outline=255, width=10)
    art.paste((170, 210, 255), (0, 0), sphere.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.6)))
    return art


def _key_out_dark(im: Image.Image) -> Image.Image:
    """A character render on a plain near-black background: make only the background transparent (the dark
    area connected to the picture's edges), so dark clothing inside the character stays solid."""
    rgb = im.convert("RGB")
    small = rgb.copy()
    small.thumbnail((600, 600))
    dark = small.convert("L").point(lambda v: 255 if v < 22 else 0)
    # flood the dark area from the border: what it reaches is background
    seeds = ([(x, 0) for x in range(0, dark.width, 7)] + [(x, dark.height - 1) for x in range(0, dark.width, 7)]
             + [(0, y) for y in range(0, dark.height, 7)] + [(dark.width - 1, y) for y in range(0, dark.height, 7)])
    for xy in seeds:
        if dark.getpixel(xy) == 255:
            ImageDraw.floodfill(dark, xy, 128)
    background = dark.point(lambda v: 255 if v == 128 else 0)
    alpha = ImageChops.invert(background.resize(rgb.size, Image.Resampling.BILINEAR)).filter(ImageFilter.GaussianBlur(1.5))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _hero(canvas: Image.Image, img: Image.Image, side: str, bottom: int) -> None:
    """A character standing left or right of the orb, cropped to head and shoulders like the series posters:
    as wide as ~45% of the poster, cut off at the band. A plain dark background is keyed out; any other
    opaque picture fades out toward the centre and the top so it blends in."""
    im = img.convert("RGBA")
    if im.getchannel("A").getextrema() == (255, 255):
        corners = [im.getpixel((x, y)) for x in (2, im.width - 3) for y in (2, im.height - 3)]
        if all(max(c[:3]) < 30 for c in corners):  # type: ignore[index]
            im = _key_out_dark(im)
    im = _trim(im)
    target_w = int(TW * 0.46)
    scale = target_w / im.width
    if im.height * scale < bottom * 0.75:  # a wide picture: fill the height instead
        scale = bottom * 0.75 / im.height
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.Resampling.LANCZOS)
    area_h = bottom - 80  # the head sits a little below the top edge
    if im.height > area_h:
        im = im.crop((0, 0, im.width, area_h))  # keep the head and shoulders; the band covers the cut
    alpha = im.getchannel("A")
    if alpha.getextrema() == (255, 255):  # still a box: soft edges toward the centre and the top
        fade = Image.new("L", im.size, 255)
        fd = ImageDraw.Draw(fade)
        inner = int(im.width * 0.35)
        for i in range(inner):
            v = int(255 * (i / inner) ** 0.8)
            x = im.width - 1 - i if side == "left" else i
            fd.line([(x, 0), (x, im.height)], fill=v)
        top = int(im.height * 0.12)
        top_fade = Image.new("L", im.size, 255)
        tfd = ImageDraw.Draw(top_fade)
        for i in range(top):
            tfd.line([(0, i), (im.width, i)], fill=int(255 * i / top))
        alpha = ImageChops.multiply(fade, top_fade)
    x = -int(im.width * 0.04) if side == "left" else TW - im.width + int(im.width * 0.04)
    y = bottom - im.height
    rim = alpha.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(18))
    canvas.paste((80, 140, 255) if side == "left" else (255, 70, 60), (x, y), rim.point(lambda v: int(v * 0.55)))
    canvas.paste(im.convert("RGB"), (x, y), alpha)


def _neon_bar(canvas: Image.Image, y: int) -> None:
    """The glossy blue band between the artwork and the text: a slanted metal strip with a bright top
    edge, a few facet highlights and a soft light spill underneath."""
    h = 120
    top_l, top_r = 26, 10  # the strip rises slightly to the right
    layer = _vgradient((TW, h), (64, 92, 220), (18, 22, 88))
    mask = Image.new("L", (TW, h), 0)
    ImageDraw.Draw(mask).polygon([(0, top_l), (TW, top_r), (TW, top_r + 46), (0, top_l + 56)], fill=235)
    # light spill below the strip
    spill = Image.new("L", (TW, h), 0)
    sd = ImageDraw.Draw(spill)
    for i in range(48):
        sd.line([(0, top_l + 56 + i), (TW, top_r + 46 + i)], fill=int(70 * (1 - i / 48) ** 2))
    canvas.paste((70, 100, 230), (0, y - 40), spill)
    canvas.paste(layer, (0, y - 40), mask)
    # facets: lighter slanted panels catching the light
    facets = Image.new("L", (TW, h), 0)
    fd = ImageDraw.Draw(facets)
    for x0, w in ((180, 260), (620, 120), (1050, 300), (1430, 110)):
        fd.polygon([(x0, top_l + 2), (x0 + w, top_r + 2), (x0 + w - 70, top_r + 44), (x0 - 70, top_l + 54)], fill=45)
    canvas.paste((200, 220, 255), (0, y - 40), facets.filter(ImageFilter.GaussianBlur(2)))
    # bright top edge with a glow, and a thin dark line at the bottom edge
    edge = Image.new("L", (TW, h), 0)
    ImageDraw.Draw(edge).line([(0, top_l), (TW, top_r)], fill=255, width=4)
    canvas.paste((120, 190, 255), (0, y - 40), edge.filter(ImageFilter.GaussianBlur(9)))
    canvas.paste((235, 245, 255), (0, y - 40), edge)
    low = Image.new("L", (TW, h), 0)
    ImageDraw.Draw(low).line([(0, top_l + 56), (TW, top_r + 46)], fill=160, width=2)
    canvas.paste((150, 170, 255), (0, y - 40), low)


def _logo(canvas: Image.Image, logo: Image.Image, box: tuple[int, int, int, int], *, align: str = "center",
          glow: bool = False) -> None:
    x0, y0, x1, y1 = box
    fitted = _fit_logo(logo, x1 - x0, y1 - y0)
    x = x0 if align == "left" else x1 - fitted.width if align == "right" else x0 + (x1 - x0 - fitted.width) // 2
    y = y0 + (y1 - y0 - fitted.height) // 2
    if glow:
        halo = fitted.getchannel("A").filter(ImageFilter.GaussianBlur(22))
        canvas.paste((0, 0, 0), (x + 6, y + 12), halo.point(lambda v: int(v * 0.75)).resize(fitted.size))
    canvas.paste(fitted, (x, y), fitted)


def _schedule_card(canvas: Image.Image, items: list[dict[str, Any]], box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    head_h = 88
    w = x1 - x0
    shadow = _rounded((w, y1 - y0), 34).filter(ImageFilter.GaussianBlur(10))
    canvas.paste((0, 0, 0), (x0 + 4, y0 + 10), shadow.point(lambda v: int(v * 0.5)))
    canvas.paste(_vgradient((w, head_h), (206, 28, 38), (160, 16, 26)), (x0, y0),
                 _rounded((w, head_h), 34, (True, True, False, False)))
    canvas.paste((255, 255, 255), (x0, y0 + head_h), _rounded((w, y1 - y0 - head_h), 34, (False, False, True, True)))
    cols = max(1, len(items))
    col_w = w / cols
    draw = ImageDraw.Draw(canvas)
    for i, item in enumerate(items):
        cx0 = int(x0 + i * col_w)
        if i:
            draw.rectangle((cx0 - 3, y0 + head_h + 18, cx0 + 3, y1 - 18), fill=(24, 24, 30))
        label = item.get("label") or ""
        _text(canvas, label, _fit(label, "SemiBold", 50, int(col_w) - 50, italic=True), x=cx0 + 26 if i == 0 else None,
              cx=None if i == 0 else cx0 + col_w / 2, top=y0 + 22, fill=(255, 255, 255))
        # Body: the big red number with up to two small lines beside it, centred in the column.
        big, lines = item.get("big") or "", [ln for ln in item.get("lines") or [] if ln]
        body_top, body_h = y0 + head_h, y1 - y0 - head_h
        big_font = _fit(big, "Black", 190, int(col_w * 0.5), italic=True) if big else None
        big_mask = _line(big, big_font)[0] if big_font else None
        small_font = _font("SemiBold", 54, italic=True)
        small = [_line(ln, small_font) for ln in lines]
        small_w = max((m.width for m, _ in small), default=0)
        gap = 22 if big_mask and small else 0
        total = (big_mask.width if big_mask else 0) + gap + small_w
        if total > col_w - 40 and small:  # shrink the small lines to fit
            scale = max(0.5, (col_w - 40 - (big_mask.width if big_mask else 0) - gap) / small_w)
            small_font = _font("SemiBold", max(24, int(54 * scale)), italic=True)
            small = [_line(ln, small_font) for ln in lines]
            small_w = max(m.width for m, _ in small)
            total = (big_mask.width if big_mask else 0) + gap + small_w
        x = int(cx0 + (col_w - total) / 2)
        if big_mask:
            _stamp(canvas, big_mask, x, int(body_top + (body_h - big_mask.height) / 2), RED)
            x += big_mask.width + gap
        stack_h = sum(m.height for m, _ in small) + 14 * (len(small) - 1)
        y = int(body_top + (body_h - stack_h) / 2)
        for j, (m, _) in enumerate(small):
            _stamp(canvas, m, x + (small_w - m.width) // 2 if not big_mask else x, y,
                   (150, 18, 28) if j == 0 else (196, 30, 40))
            y += m.height + 14


def compose(art: Image.Image, poster: dict[str, Any], logos: dict[str, Image.Image]) -> Image.Image:
    """Lay the tournament poster out on 1600x2000. `logos` maps asset id -> image (logos and the venue photo)."""
    from app.agents.poster import art_placement

    canvas = _background(art, art_placement(poster))
    draw = ImageDraw.Draw(canvas)
    get = lambda key: logos.get(str(poster.get(key) or ""))  # noqa: E731
    sponsors = [logos[str(i)] for i in poster.get("sponsor_logo_ids", []) if str(i) in logos]
    organisers = [logos[str(i)] for i in poster.get("presenter_logo_ids", []) if str(i) in logos]

    # Top: characters, event logo (or the typeset title), organisers' logos, game logo over the orb.
    for hero_side, key in (("left", "hero_left_id"), ("right", "hero_right_id")):
        if (hero := get(key)) is not None:
            _hero(canvas, hero, hero_side, bottom=640)
    if (event := get("event_logo_id")) is not None:
        _logo(canvas, event, (34, 22, 640, 290), align="left", glow=True)
    else:
        title = (poster.get("title") or "").upper()
        h = _text(canvas, title, _fit(title, "Black", 120, 600, italic=True), x=56, top=50, fill=((255, 255, 255), (200, 214, 255)),
                  outline=(10, 14, 40), outline_px=4, glow=(40, 110, 255), glow_px=14)
        if edition := poster.get("edition"):
            _text(canvas, edition, _font("Black", 96, italic=True), x=250, top=50 + h + 6, fill=GOLD,
                  outline=(40, 20, 0), outline_px=3, glow=(255, 170, 40), glow_px=10)
    if organisers:
        _logo_row(canvas, organisers, (980, 40, 1570, 210), gap=36)
    if (game := get("game_logo_id")) is not None:
        _logo(canvas, game, (300, 250, 1300, 600), glow=True)  # wide wordmarks get the room they need
    _neon_bar(canvas, 655)

    # Prize block.
    subtitle = poster.get("subtitle") or ""
    _text(canvas, subtitle, _fit(subtitle, "SemiBold", 62, TW - 120), cx=TW / 2, top=694, fill=(255, 255, 255),
          glow=(0, 0, 20), glow_px=8)
    label = poster.get("prize_label") or ""
    _text(canvas, label, _fit(label, "Black", 112, TW - 220), cx=TW / 2, top=762, fill=(52, 10, 22),
          outline=(255, 255, 255), outline_px=7, glow=(0, 0, 0), glow_px=10)
    amount = poster.get("prize_amount") or ""
    if amount:
        font = _fit(amount, "SemiBold", 250, TW - 180)
        mask, _ = _line(amount, font)
        x, y = int(TW / 2 - mask.width / 2), 1094 - mask.height
        _stamp(canvas, mask, x, y, GOLD, outline=(120, 40, 20), outline_px=4, glow=(255, 190, 90), glow_px=12,
               glow_alpha=0.45)
        _sparkle(canvas, x + 40, y + 24)
    reg = poster.get("registration_text") or ""
    _text(canvas, reg, _fit(reg, "Medium", 70, TW - 140), cx=TW / 2, top=1140, fill=(255, 255, 255))

    # Free-entry pill and the category lines beside it.
    if badge := poster.get("badge_text") or "":
        pill_w, pill_h = 700, 118
        canvas.paste(_vgradient((pill_w, pill_h), (206, 30, 40), (122, 12, 22)), (24, 1232), _rounded((pill_w, pill_h), 34))
        font = _fit(badge, "SemiBold", 70, pill_w - 60)
        mask, _ = _line(badge, font)
        _stamp(canvas, mask, 24 + (pill_w - mask.width) // 2, 1232 + (pill_h - mask.height) // 2,
               ((255, 244, 150), (246, 196, 60)), outline=(70, 10, 10), outline_px=3)
    lines = [ln for ln in poster.get("category_lines") or [] if ln]
    if lines:
        cat_x0 = 750 if badge else 60
        cx = (cat_x0 + TW - 24) / 2
        top = 1240 if len(lines) > 1 else 1262
        # One size for every line (the longest decides), so the lines read as a block.
        size = min(_fit(ln.replace("*", ""), "Medium", 50, TW - 60 - cat_x0).size for ln in lines)
        font = _font("Medium", int(size))
        for ln in lines:
            top += _text(canvas, ln, font, cx=cx, top=top, highlight=YELLOW) + 20

    # Schedule card (left) and the QR code to register (right).
    qr_url = poster.get("qr_url") or ""
    card_right = 1110 if qr_url else TW - 24
    items = [i for i in poster.get("schedule") or [] if i.get("label") or i.get("big") or i.get("lines")]
    if items:
        _schedule_card(canvas, items, (24, 1384, card_right, 1650))
    if qr_url:
        cta = poster.get("cta_text") or ""
        _text(canvas, cta, _fit(cta, "Black", 104, 440, italic=True), cx=1362, top=1372, fill=GOLD,
              outline=(60, 24, 4), outline_px=4, glow=(0, 0, 0), glow_px=8)
        box = (1150, 1500, 1574, 1836)
        side = min(box[2] - box[0], box[3] - box[1])
        bx = box[0] + (box[2] - box[0] - side) // 2
        draw.rectangle((bx, box[1], bx + side, box[1] + side), fill=(255, 255, 255))
        canvas.paste(_qr(qr_url, side - 36), (bx + 18, box[1] + 18))

    # Venue: a photo of the place at the bottom-left, fading into the navy, and the venue lines.
    if (photo := get("venue_photo_id")) is not None:
        pw, ph = 400, 210
        img = _cover(photo, pw, ph)
        alpha = Image.new("L", (pw, ph), 0)
        for xx in range(pw):
            ImageDraw.Draw(alpha).line([(xx, 0), (xx, ph)], fill=int(255 * min(1.0, (pw - xx) / 120)))
        top_fade = Image.linear_gradient("L").resize((pw, ph)).point(lambda v: min(255, v * 3))
        canvas.paste(img, (0, 1855 - ph), ImageChops.multiply(alpha, top_fade))
    text_x0 = 400 if photo is not None else 60
    text_x1 = 1120 if qr_url else TW - 60
    venue, detail = poster.get("venue") or "", poster.get("venue_detail") or ""
    vcx = (text_x0 + text_x1) / 2
    vtop = 1690 if detail else 1720
    h = _text(canvas, venue, _fit(venue, "SemiBold", 84, text_x1 - text_x0, italic=True), cx=vcx, top=vtop,
              fill=(255, 255, 255), glow=(0, 0, 0), glow_px=6)
    _text(canvas, detail, _fit(detail, "SemiBold", 50, text_x1 - text_x0, italic=True), cx=vcx, top=vtop + h + 18,
          fill=(255, 255, 255), glow=(0, 0, 0), glow_px=6)

    # Sponsor strip.
    draw.rectangle((0, 1855, TW, TH), fill=(0, 0, 0))
    if sponsors:
        _logo_row(canvas, [_trim(s) for s in sponsors], (70, 1880, TW - 70, 1975), gap=56)
    return canvas


def _sparkle(canvas: Image.Image, x: int, y: int) -> None:
    """A four-point star with a warm glow and a long horizontal lens flare, like the series posters."""
    flare = Image.new("L", (900, 60), 0)
    ImageDraw.Draw(flare).ellipse((0, 26, 900, 34), fill=255)
    flare = flare.filter(ImageFilter.GaussianBlur(3))
    canvas.paste((255, 226, 160), (x - 620, y - 30), flare.point(lambda v: int(v * 0.8)))
    size, c = 480, 240
    star = Image.new("L", (size, size), 0)
    ImageDraw.Draw(star).polygon([(c, c - 120), (c + 8, c - 8), (c + 120, c), (c + 8, c + 8), (c, c + 120),
                                  (c - 8, c + 8), (c - 120, c), (c - 8, c - 8)], fill=255)
    star = star.filter(ImageFilter.GaussianBlur(3))
    glow = star.filter(ImageFilter.GaussianBlur(22))
    canvas.paste((255, 214, 120), (x - c, y - c), glow)
    canvas.paste((255, 250, 230), (x - c, y - c), star)


def placeholder() -> Image.Image:
    """Stand-in artwork for the live preview: the blue/red split with an orb, clearly not final art."""
    w, h = 400, 267
    art = Image.new("RGB", (w, h))
    for x in range(w):
        t = x / (w - 1)
        ImageDraw.Draw(art).line([(x, 0), (x, h)], fill=(int(20 + 150 * t), int(40 - 20 * t), int(150 - 110 * t)))
    orb = Image.new("L", (w, h), 0)
    ImageDraw.Draw(orb).ellipse((140, 50, 260, 170), fill=230)
    art.paste((220, 235, 255), (0, 0), orb.filter(ImageFilter.GaussianBlur(26)))
    figs = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(figs)
    for cx in (70, 330):
        d.ellipse((cx - 28, 60, cx + 28, 124), fill=150)
        d.polygon([(cx - 80, h), (cx - 50, 130), (cx + 50, 130), (cx + 80, h)], fill=150)
    art.paste((12, 10, 20), (0, 0), figs.filter(ImageFilter.GaussianBlur(3)))
    return art.resize((TW, ART_H), Image.Resampling.BICUBIC)
