"""Event poster layout: the AI paints only the artwork; every word and logo is typeset here.

Image models cannot write Thai (or reliably any text) and must never redraw someone's logo, so the
poster is composed from three layers: the generated artwork, a dark grade that makes room for text,
and the typeset content (title, date banner, headline, venue, time) plus the organization's own logos.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from app.services.media import find_display_fonts, find_fonts
from app.services.thai_text import draw_text

W, H = 1080, 1920
MARGIN = 60


@dataclass(frozen=True)
class Theme:
    light: tuple[int, int, int]  # top of gradient text
    accent: tuple[int, int, int]  # bottom of gradient text
    glow: tuple[int, int, int]
    shade: tuple[int, int, int]  # dark grade behind the text
    ink: tuple[int, int, int]  # text on white banners
    prompt: str


THEMES = {
    "gold": Theme((255, 244, 196), (240, 178, 52), (255, 160, 30), (14, 10, 4), (52, 36, 8),
                  "warm golden light, amber and deep black, glowing gold dust and sparks"),
    "neon": Theme((220, 252, 255), (70, 210, 255), (0, 160, 255), (4, 8, 24), (8, 30, 70),
                  "electric cyan and magenta neon light, dark navy, holographic sparks"),
    "crimson": Theme((255, 226, 214), (255, 92, 80), (255, 40, 50), (18, 4, 6), (80, 10, 20),
                  "crimson red and deep black, fiery embers and smoke"),
}
DEFAULT_ART = "an original heroic esports champion character in futuristic armor, striking a dynamic action pose"


def is_tournament(poster: dict[str, Any]) -> bool:
    return poster.get("template") == "tournament"


def uses_heroes(poster: dict[str, Any]) -> bool:
    """A tournament poster with the user's own characters: the app draws the backdrop, no AI image."""
    return is_tournament(poster) and bool(poster.get("hero_left_id") or poster.get("hero_right_id"))


def art_aspect(poster: dict[str, Any]) -> str:
    """The aspect ratio to ask the image provider for: tall key art, or a wide banner for the tournament layout."""
    return "16:9" if is_tournament(poster) else "9:16"


def build_prompt(poster: dict[str, Any], art: str | None) -> str:
    if is_tournament(poster):
        from app.agents import poster_tournament

        return poster_tournament.build_prompt(poster, art)
    theme = THEMES.get(poster.get("theme", "gold"), THEMES["gold"])
    return (
        f"Epic esports tournament key art, game splash-art quality. {art or DEFAULT_ART}. "
        "Original character design, not based on any existing game or franchise. "
        f"Dramatic cinematic rim lighting, {theme.prompt}, particles and lens flares, dynamic diagonal composition. "
        "Main subject in the middle of the frame; simple dark sky in the top quarter; darker, less detailed lower part. "
        "Highly detailed digital painting, sharp focus. No text, no letters, no logos, no watermarks."
    )


# --- text helpers ------------------------------------------------------------------
def _mask(text: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    """White-on-black mask of one line, cropped to the ink (Thai tone marks included)."""
    ascent, descent = font.getmetrics()
    pad = int(font.size * 0.4)
    width = int(font.getlength(text)) + 2 * pad
    m = Image.new("L", (max(width, 1), ascent + descent + 2 * pad), 0)
    draw_text(ImageDraw.Draw(m), (pad, pad), text, font, 255)
    box = m.getbbox()
    return m.crop(box) if box else m


def _fit(text: str, path: str, max_size: int, max_width: int, min_size: int = 28) -> ImageFont.FreeTypeFont:
    size = max_size
    while size > min_size:
        font = ImageFont.truetype(path, size)
        if font.getlength(text) <= max_width:
            return font
        size -= 4
    return ImageFont.truetype(path, min_size)


def _vgradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    strip = Image.new("RGB", (1, max(h, 1)))
    for y in range(h):
        t = y / max(h - 1, 1)
        strip.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return strip.resize((max(w, 1), max(h, 1)))


def _paste_text(canvas: Image.Image, mask: Image.Image, center_x: float, top: int, *, fill: Any,
                outline: tuple[int, int, int] | None = None, outline_px: int = 0,
                glow: tuple[int, int, int] | None = None, glow_px: int = 0) -> int:
    """Composite one line (solid colour or gradient fill) with optional outline and glow. Returns its height."""
    pad = max(outline_px, glow_px * 2, 1)
    m = Image.new("L", (mask.width + 2 * pad, mask.height + 2 * pad), 0)
    m.paste(mask, (pad, pad))
    x, y = int(center_x - m.width / 2), top - pad
    if glow and glow_px:
        halo = m.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(glow_px))
        canvas.paste(Image.new("RGB", m.size, glow), (x, y), halo.point(lambda v: int(v * 0.85)))
    if outline and outline_px:
        canvas.paste(Image.new("RGB", m.size, outline), (x, y), m.filter(ImageFilter.MaxFilter(2 * outline_px + 1)))
    face = _vgradient(m.size, *fill) if isinstance(fill, tuple) and isinstance(fill[0], tuple) else Image.new("RGB", m.size, fill)
    canvas.paste(face, (x, y), m)
    return mask.height


# --- logos ---------------------------------------------------------------------------
def _trim(logo: Image.Image) -> Image.Image:
    logo = logo.convert("RGBA")
    box = logo.getchannel("A").getbbox()
    return logo.crop(box) if box else logo


def _fit_logo(logo: Image.Image, max_w: int, max_h: int) -> Image.Image:
    logo = _trim(logo)
    scale = min(max_w / logo.width, max_h / logo.height)
    return logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))), Image.Resampling.LANCZOS)


def _logo_row(canvas: Image.Image, logos: list[Image.Image], box: tuple[int, int, int, int], gap: int = 36) -> None:
    """Lay logos out side by side, centred in box, all the same height (shrunk to fit the width)."""
    if not logos:
        return
    x0, y0, x1, y1 = box
    height = y1 - y0
    trimmed = [_trim(lg) for lg in logos]
    widths = [lg.width * height / lg.height for lg in trimmed]
    total = sum(widths) + gap * (len(trimmed) - 1)
    scale = min(1.0, (x1 - x0) / total)
    h = max(1, int(height * scale))
    x = x0 + ((x1 - x0) - total * scale) / 2
    for lg, w in zip(trimmed, widths, strict=True):
        fitted = lg.resize((max(1, int(w * scale)), h), Image.Resampling.LANCZOS)
        canvas.paste(fitted, (int(x), y0 + (height - h) // 2), fitted)
        x += w * scale + gap * scale


# --- layout -----------------------------------------------------------------------------
def _cover(art: Image.Image) -> Image.Image:
    art = art.convert("RGB")
    scale = max(W / art.width, H / art.height)
    art = art.resize((int(art.width * scale + 0.5), int(art.height * scale + 0.5)), Image.Resampling.LANCZOS)
    left, top = (art.width - W) // 2, (art.height - H) // 2
    return art.crop((left, top, left + W, top + H))


def frame_art(art: Image.Image, w: int, h: int, zoom: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> Image.Image:
    """Fit artwork into a w x h frame: zoom 1 covers the frame; dx / dy move it by a share of the frame
    (positive = right / down). Uncovered edges are filled with a blurred, enlarged copy of the art."""
    art = art.convert("RGB")
    base = max(w / art.width, h / art.height)
    if zoom == 1.0 and dx == 0.0 and dy == 0.0:
        scale = base
        img = art.resize((int(art.width * scale + 0.5), int(art.height * scale + 0.5)), Image.Resampling.LANCZOS)
        left, top = (img.width - w) // 2, (img.height - h) // 2
        return img.crop((left, top, left + w, top + h))
    backdrop = frame_art(art, w, h).filter(ImageFilter.GaussianBlur(36))
    scale = base * zoom
    img = art.resize((max(1, int(art.width * scale + 0.5)), max(1, int(art.height * scale + 0.5))),
                     Image.Resampling.LANCZOS)
    x = int((w - img.width) / 2 + dx * w)
    y = int((h - img.height) / 2 + dy * h)
    backdrop.paste(img, (x, y))
    return backdrop


def art_placement(poster: dict[str, Any]) -> tuple[float, float, float]:
    """The user's artwork placement: (zoom, x, y); (1, 0, 0) means "as framed / automatic"."""
    return (float(poster.get("art_zoom") or 1.0), float(poster.get("art_x") or 0.0), float(poster.get("art_y") or 0.0))


def _subject_top(art: Image.Image) -> int | None:
    """Where the main subject starts, in px from the top: the first band of rows with much more detail
    than the plain sky the prompt asks for. None when the artwork has no such contrast (busy all over)."""
    cols, rows = 108, 192
    edges = art.convert("L").resize((cols, rows), Image.Resampling.BILINEAR).filter(ImageFilter.FIND_EDGES)
    px = edges.tobytes()
    energy = [sum(px[y * cols + 1:(y + 1) * cols - 1]) / (cols - 2) for y in range(1, rows - 1)]
    smooth = [sum(energy[max(0, i - 2):i + 3]) / len(energy[max(0, i - 2):i + 3]) for i in range(len(energy))]
    sky = sorted(smooth[:18])[9]
    peak = sorted(smooth)[int(len(smooth) * 0.9)]
    if peak - sky < 4:
        return None
    threshold = sky + 0.35 * (peak - sky)
    for i in range(8, int(len(smooth) * 0.7)):
        if smooth[i] > threshold:
            return (i + 1) * H // rows
    return None


def _art_shift(art: Image.Image, title_bottom: int, text_top: int) -> int:
    """How far to move the artwork so the subject starts just under the title block: down when the
    model put it behind the title, up when it left an empty gap. Falls back to a fixed drop."""
    head = _subject_top(art)
    if head is None:
        return 200
    shift = title_bottom + 60 - head
    shift = min(shift, text_top - 420 - head)  # keep the subject above the date banner
    return max(-260, min(380, shift))


def _lower(art: Image.Image, shift: int) -> Image.Image:
    """Move the artwork down by `shift` px so the subject's face clears the title block (or up, for a
    negative shift, to close an empty gap under the title).

    The gap that opens is filled with a blurred mirror of the artwork's edge: at the top it ends up
    under the dark title grade and reads as sky; at the bottom it is behind the text block.
    """
    if shift == 0:
        return art
    if shift < 0:
        up = -shift
        out = Image.new("RGB", (W, H))
        out.paste(art.crop((0, up, W, H)), (0, 0))
        edge = art.crop((0, H - up, W, H)).transpose(Image.Transpose.FLIP_TOP_BOTTOM).filter(ImageFilter.GaussianBlur(28))
        out.paste(edge, (0, H - up))
        return out
    out = Image.new("RGB", (W, H))
    out.paste(art.crop((0, 0, W, H - shift)), (0, shift))
    edge = art.crop((0, 0, W, shift)).transpose(Image.Transpose.FLIP_TOP_BOTTOM).filter(ImageFilter.GaussianBlur(28))
    out.paste(edge, (0, 0))
    # Soften the join: fade from blurred to sharp over the first 80 px of the artwork.
    fade = ImageChops.invert(Image.linear_gradient("L").resize((W, 80)))
    out.paste(art.crop((0, 0, W, 80)).filter(ImageFilter.GaussianBlur(28)), (0, shift), fade)
    return out


def _grade(canvas: Image.Image, theme: Theme, text_top: int) -> Image.Image:
    """Darken the top (title) and everything below text_top, so the type stays readable on any artwork."""
    alpha = Image.new("L", (1, H))
    fade = 260
    for y in range(H):
        a = 0.0
        if y < 420:
            a = 0.62 * (1 - y / 420) ** 1.4
        if y > text_top - fade:
            a = max(a, min(0.93, 0.93 * ((y - (text_top - fade)) / fade) ** 1.2))
        alpha.putpixel((0, y), int(255 * a))
    shade = Image.new("RGB", (W, H), theme.shade)
    return Image.composite(shade, canvas, alpha.resize((W, H)))


def _draw_title(canvas: Image.Image, poster: dict[str, Any], theme: Theme, top_y: int,
                event_logo: Image.Image | None, display: str, bold: str) -> int:
    """The title block: the event logo if there is one, otherwise typeset tag / title / edition badge.
    Returns the y of its bottom edge."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    if event_logo is not None:
        fitted = _fit_logo(event_logo, W - 2 * MARGIN - 40, 400)
        glow = fitted.getchannel("A").filter(ImageFilter.GaussianBlur(18))
        x = (W - fitted.width) // 2
        canvas.paste(Image.new("RGB", fitted.size, (0, 0, 0)), (x + 6, top_y + 10), glow.point(lambda v: int(v * 0.7)))
        canvas.paste(fitted, (x, top_y), fitted)
        return top_y + fitted.height
    if tag := poster.get("tag", ""):
        spaced = " ".join(tag.upper()) if len(tag) <= 8 else tag.upper()
        m = _mask(spaced, ImageFont.truetype(bold, 50))
        top_y += _paste_text(canvas, m, W / 2, top_y, fill=(255, 255, 255), glow=(0, 0, 0), glow_px=6) + 18
    title = poster.get("title", "")
    m = _mask(title.upper(), _fit(title.upper(), display, 170, W - 2 * MARGIN))
    top_y += _paste_text(canvas, m, W / 2, top_y, fill=((255, 255, 255), (226, 226, 232)),
                         outline=(10, 10, 14), outline_px=3, glow=theme.glow, glow_px=16)
    if edition := poster.get("edition", ""):
        top_y += 26
        m = _mask(edition, ImageFont.truetype(display, 64))
        bw, bh = m.width + 120, m.height + 34
        bx = (W - bw) // 2
        draw.polygon([(bx + 22, top_y), (bx + bw, top_y), (bx + bw - 22, top_y + bh), (bx, top_y + bh)],
                     fill=(255, 255, 255, 255))
        _paste_text(canvas, m, W / 2, top_y + 17, fill=(14, 14, 18))
        top_y += bh
    return top_y


def compose(art: Image.Image, poster: dict[str, Any], logos: dict[str, Image.Image], *,
            lower: bool = True) -> Image.Image:
    """Lay the poster out on 1080x1920. `logos` maps asset id -> image; unknown ids are skipped.

    `lower` moves AI artwork down under the title; artwork the user made themselves is used as framed.
    The tournament template has its own 4:5 layout (see poster_tournament)."""
    if is_tournament(poster):
        from app.agents import poster_tournament

        return poster_tournament.compose(art, poster, logos)
    theme = THEMES.get(poster.get("theme", "gold"), THEMES["gold"])
    display, strong = find_display_fonts()
    _, bold = find_fonts()
    sponsors = [logos[i] for i in poster.get("sponsor_logo_ids", []) if i in logos]
    presenters = [logos[i] for i in poster.get("presenter_logo_ids", []) if i in logos]
    event_logo = logos.get(poster.get("event_logo_id") or "")

    # Measure the bottom block first (bottom-up), so the grade starts right above it.
    date_text, venue, when = poster.get("date_text", ""), poster.get("venue", ""), poster.get("time_text", "")
    headline = [line for line in poster.get("headline", []) if line]
    y = H - 70
    blocks: list[tuple[str, int, Any]] = []  # (kind, top, data), filled bottom-up
    if presenters:
        y -= 150
        blocks.append(("presenters", y, None))
        label_font = _fit(poster.get("presenter_label") or "", bold, 38, 600)
        y -= 64
        blocks.append(("presenter_label", y, label_font))
    if when:
        font = _fit(when, display, 132, W - 2 * MARGIN)
        m = _mask(when, font)
        y -= m.height + 36
        blocks.append(("time", y, m))
    if venue:
        font = _fit(venue, strong, 60, W - 2 * MARGIN - 70)
        m = _mask(venue, font)
        y -= m.height + 34
        blocks.append(("venue", y, m))
    if headline and (venue or when):
        y -= 34
        blocks.append(("divider", y, None))
    for line in reversed(headline):
        font = _fit(line, display, 104, W - 2 * MARGIN)
        m = _mask(line, font)
        y -= m.height + 22
        blocks.append(("headline", y, m))
    if date_text:
        font = _fit(date_text, display, 76, W - 2 * MARGIN - 120)
        m = _mask(date_text, font)
        y -= m.height + 66 + 26
        blocks.append(("date", y, m))
    text_top = max(y, int(H * 0.45))

    # The title block sits under the sponsor bar (if any); measure it on a scratch canvas so the
    # artwork can be moved to start right below it (models put the subject anywhere in the top half).
    bar_h = 116
    top_y = bar_h + 60 if sponsors else 70
    zoom, ax, ay = art_placement(poster)
    placed = (zoom, ax, ay) != (1.0, 0.0, 0.0)
    covered = frame_art(art, W, H, zoom, ax, ay)
    shift = 0
    if lower and not placed:  # the user placed the art themselves: no automatic shift
        title_bottom = _draw_title(Image.new("RGB", (W, H)), poster, theme, top_y, event_logo, display, bold)
        shift = _art_shift(covered, title_bottom, text_top)
    canvas = _grade(_lower(covered, shift), theme, text_top)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Sponsor bar: a white panel with a slanted edge across the top.
    if sponsors:
        shadow = Image.new("L", (W, bar_h + 40), 0)
        ImageDraw.Draw(shadow).polygon([(30, 0), (W, 0), (W, bar_h + 6), (70, bar_h + 6)], fill=150)
        canvas.paste((0, 0, 0), (0, 0, W, bar_h + 40), shadow.filter(ImageFilter.GaussianBlur(12)))
        draw.polygon([(24, 0), (W, 0), (W, bar_h), (64, bar_h)], fill=(255, 255, 255, 255))
        _logo_row(canvas, sponsors, (90, 22, W - 34, bar_h - 22), gap=40)

    _draw_title(canvas, poster, theme, top_y, event_logo, display, bold)

    # Bottom block, top-down now that every position is known.
    gradient_fill = (theme.light, theme.accent)
    for kind, top, data in reversed(blocks):
        if kind == "date":
            bh = data.height + 66
            draw.polygon([(MARGIN + 30, top), (W - MARGIN + 10, top), (W - MARGIN - 20, top + bh), (MARGIN, top + bh)],
                         fill=(255, 255, 255, 250))
            _paste_text(canvas, data, W / 2, top + 33, fill=theme.ink)
        elif kind == "headline":
            _paste_text(canvas, data, W / 2, top, fill=gradient_fill, outline=(20, 12, 4), outline_px=4,
                        glow=theme.glow, glow_px=10)
        elif kind == "divider":
            line = Image.new("L", (W - 2 * MARGIN, 1))
            for x in range(line.width):
                t = abs(x / line.width - 0.5) * 2
                line.putpixel((x, 0), int(255 * max(0.0, 1 - t ** 2)))
            line = line.resize((W - 2 * MARGIN, 3))
            canvas.paste(Image.new("RGB", line.size, theme.accent), (MARGIN, top), line)
        elif kind == "venue":
            pin_x = int(W / 2 + 26 - data.width / 2 - 34)
            cy = top + data.height // 2 - 8
            draw.ellipse((pin_x - 18, cy - 18, pin_x + 18, cy + 18), fill=(232, 52, 52, 255))
            draw.polygon([(pin_x - 14, cy + 10), (pin_x + 14, cy + 10), (pin_x, cy + 40)], fill=(232, 52, 52, 255))
            draw.ellipse((pin_x - 7, cy - 7, pin_x + 7, cy + 7), fill=(255, 255, 255, 255))
            _paste_text(canvas, data, W / 2 + 26, top, fill=(255, 255, 255), outline=(10, 8, 6), outline_px=3)
        elif kind == "time":
            _paste_text(canvas, data, W / 2, top, fill=gradient_fill, outline=(20, 12, 4), outline_px=4,
                        glow=theme.glow, glow_px=14)
        elif kind == "presenter_label":
            m = _mask(poster.get("presenter_label") or "", data)
            canvas.paste((255, 255, 255), (MARGIN + 10, top), m)
        elif kind == "presenters":
            _logo_row(canvas, presenters, (MARGIN, top, W - MARGIN, top + 130), gap=44)
    return canvas


@lru_cache(maxsize=4)
def _placeholder(theme_name: str) -> Image.Image:
    """Stand-in artwork for the live preview: a themed glow with a soft figure, clearly not the final art."""
    theme = THEMES.get(theme_name, THEMES["gold"])
    small = (270, 480)
    art = _vgradient(small, tuple(c // 3 for c in theme.accent), theme.shade)  # type: ignore[arg-type]
    glow = Image.new("L", small, 0)
    d = ImageDraw.Draw(glow)
    d.ellipse((20, 60, 250, 330), fill=200)
    art.paste(Image.new("RGB", small, theme.glow), (0, 0), glow.filter(ImageFilter.GaussianBlur(40)))
    figure = Image.new("L", small, 0)
    d = ImageDraw.Draw(figure)
    d.ellipse((108, 120, 162, 180), fill=150)  # head
    d.polygon([(70, 400), (95, 200), (175, 200), (200, 400)], fill=150)  # shoulders and body
    art.paste(Image.new("RGB", small, (16, 12, 10)), (0, 0), figure.filter(ImageFilter.GaussianBlur(3)))
    return art.resize((W, H), Image.Resampling.BICUBIC)


def preview(poster: dict[str, Any], logos: dict[str, Image.Image], width: int = 540,
            art: Image.Image | None = None, lower: bool = False) -> bytes:
    """A quick JPEG of the layout for the poster form's live preview: over your own artwork when one is
    chosen, otherwise over stand-in artwork."""
    if art is None and is_tournament(poster):
        from app.agents import poster_tournament

        art = poster_tournament.space_art() if uses_heroes(poster) else poster_tournament.placeholder()
    if art is not None:
        img = compose(art, poster, logos, lower=lower)
    else:
        img = compose(_placeholder(poster.get("theme", "gold")), poster, logos)
    img = img.resize((width, int(width * img.height / img.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=84)
    return buf.getvalue()


def difference(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute pixel difference (0-255); used by tests to check a layer was drawn."""
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    hist = diff.histogram()
    return sum(i * n for i, n in enumerate(hist)) / max(1, sum(hist))
