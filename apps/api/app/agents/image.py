"""Image Agent: real image files via local ComfyUI or an authorized cloud image API."""
from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.agents import poster as poster_layout
from app.agents.base import AgentContext
from app.errors import AgentError
from app.services.media import find_fonts
from app.services.router import route_image

DEMO_SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


def build_prompt(ctx: AgentContext, concept: str) -> str:
    b = ctx.brand
    style = b.get("visual_style", "modern creative technology aesthetic, clean composition, soft cinematic lighting")
    return (
        f"Professional promotional key visual for a business software product. {concept}. "
        f"Style: {style}. Color palette: deep navy background tones with blue ({b['primary_color']}) and "
        f"cyan ({b['accent_color']}) accents. Leave uncluttered negative space for a headline. "
        "High detail, sharp focus. No text, no letters, no logos, no watermarks."
    )


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    w, h = size
    c1, c2 = _hex(top), _hex(bottom)
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    assert px is not None
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))  # type: ignore[assignment]
    return strip.resize((w, h))


def demo_image(ctx: AgentContext, index: int, aspect_ratio: str) -> bytes:
    """Locally rendered placeholder, unmistakably labelled. Only used in explicit DEMO_MODE."""
    size = DEMO_SIZES.get(aspect_ratio, DEMO_SIZES["16:9"])
    b = ctx.brand
    img = gradient(size, b["background_color"], b["primary_color"])
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = size
    for i in range(6):
        r = int(min(w, h) * (0.18 + 0.09 * i))
        cx, cy = int(w * (0.15 + 0.14 * ((index + i) % 6))), int(h * (0.25 + 0.1 * (i % 4)))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(*_hex(b["accent_color"]), 70), width=4)
    regular, bold = find_fonts()
    big = ImageFont.truetype(bold, int(min(w, h) * 0.085))
    small = ImageFont.truetype(regular, int(min(w, h) * 0.035))
    draw.text((w / 2, h * 0.42), "DEMO PLACEHOLDER", font=big, fill=(255, 255, 255, 235), anchor="mm")
    draw.text((w / 2, h * 0.52), f"{b['product']} · image {index} · not AI-generated", font=small,
              fill=(255, 255, 255, 200), anchor="mm")
    draw.text((w / 2, h * 0.58), "Connect ComfyUI or a cloud image provider to generate real artwork", font=small,
              fill=(*_hex(b["accent_color"]), 230), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def load_logos(ctx: AgentContext, poster: dict[str, Any]) -> tuple[dict[str, Image.Image], int]:
    """The poster's logos from the organization's library: (id -> image, number that no longer exist)."""
    from app.services.brand_assets import load_logos as load

    return load(ctx.session, ctx.storage, ctx.org.id, poster)


def run(ctx: AgentContext) -> dict[str, Any]:
    p = ctx.task.params
    index = int(p.get("index", 1))
    aspect = p.get("aspect_ratio", "16:9")
    poster = p.get("poster")
    if poster:
        # Event poster: the provider paints only the artwork; text and logos are typeset afterwards.
        aspect = poster_layout.art_aspect(poster)
        prompt = poster_layout.build_prompt(poster, p.get("concept"))
    else:
        prompt = build_prompt(ctx, p.get("concept", "abstract technology background"))

    art_id = (poster or {}).get("art_asset_id")
    heroes = bool(poster) and not art_id and poster_layout.uses_heroes(poster or {})
    own_art = bool(art_id) or heroes
    extra: dict[str, Any] = {}
    if heroes:
        # The user's own characters on the app's space backdrop: nothing to generate.
        from app.agents import poster_tournament
        from app.services.router import Route

        route = ctx.use_route(Route("uploaded", "local", "your own characters on the poster backdrop; no AI image, no cost"))
        buf = io.BytesIO()
        poster_tournament.space_art().save(buf, "PNG")
        raw = buf.getvalue()
        prompt = ""
        extra["artwork"] = "composed"
    elif own_art:
        # The user's own artwork (e.g. made in Adobe Firefly or Photoshop): no AI image is generated.
        from app.services.brand_assets import load_artwork
        from app.services.router import Route

        art = load_artwork(ctx.session, ctx.storage, ctx.org.id, art_id)
        if art is None:
            raise AgentError("ASSET_NOT_FOUND", "The poster's artwork was deleted from the artwork library; "
                             "choose another artwork or let the AI paint one")
        from app.services.brand_assets import artwork_source

        # Artwork saved from an earlier AI poster is laid out like fresh AI art (subject under the title);
        # the user's own artwork is used as framed.
        reused = artwork_source(ctx.session, ctx.org.id, art_id) == "ai"
        route = ctx.use_route(Route("uploaded", "local", "artwork from your library; no AI image generated, no cost"))
        buf = io.BytesIO()
        art.save(buf, "PNG")
        raw = buf.getvalue()
        prompt = ""
        extra["artwork"] = "reused" if reused else "uploaded"
        extra["artwork_asset_id"] = str(art_id)
    else:
        route = ctx.use_route(route_image(ctx.session, ctx.job, ctx.org))
    if own_art:
        pass  # artwork already loaded above
    elif route.demo:
        raw = demo_image(ctx, index, aspect)
    elif route.comfyui is not None:
        raw, extra = route.comfyui.generate(prompt, aspect, on_wait=ctx.heartbeat)
        ctx.record_usage("comfyui", "image", {"images": 1}, route.estimated_cost_usd)
    elif route.firefly is not None:
        from app.providers.comfyui import NEGATIVE

        raw, extra = route.firefly.generate_image(prompt, aspect, negative=NEGATIVE, on_wait=ctx.heartbeat)
        ctx.record_usage("firefly", "image", {"images": 1, "size": extra.get("firefly_size")}, route.estimated_cost_usd)
    elif route.openai is not None:
        raw = route.openai.generate_image(prompt, aspect)
        ctx.record_usage("openai", "image", {"images": 1}, ctx.settings.image_usd_per_image)
    else:  # pragma: no cover - router always returns one of the above
        raise AgentError("ROUTING_ERROR", "No image provider selected")

    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            rgb = im.convert("RGB")
            # Providers only offer a few native sizes; centre-crop to the exact requested aspect ratio.
            aw, ah = (int(x) for x in aspect.split(":"))
            w, h = rgb.size
            if abs(w / h - aw / ah) > 0.02:
                if w / h > aw / ah:
                    new_w = int(h * aw / ah)
                    rgb = rgb.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
                else:
                    new_h = int(w * ah / aw)
                    rgb = rgb.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
                extra["cropped_from"] = f"{w}x{h}"
            if poster and not own_art and not route.demo:
                # Keep the AI artwork, so the poster can be re-laid out later without new artwork.
                from app.services.brand_assets import save_ai_artwork

                try:
                    kept = save_ai_artwork(ctx.session, ctx.storage, ctx.org.id, rgb,
                                           f"AI art · {poster.get('title', '')} {poster.get('edition', '')} #{index}")
                except Exception:  # noqa: BLE001 - keeping a copy must never fail the poster itself
                    kept = None
                if kept is not None:
                    extra["artwork"] = "ai"
                    extra["artwork_asset_id"] = str(kept.id)
            if poster:
                logos, missing = load_logos(ctx, poster)
                extra["artwork_size"] = f"{rgb.width}x{rgb.height}"
                if missing:
                    extra["missing_logos"] = missing
                rgb = poster_layout.compose(rgb, poster, logos,
                                            lower=not own_art or extra.get("artwork") == "reused")
            width, height = rgb.size
            buf = io.BytesIO()
            rgb.save(buf, "PNG")
            png = buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise AgentError("IMAGE_INVALID", f"Provider returned data that is not a valid image: {exc}", retryable=True) from exc

    if poster:
        extra["poster"] = {"template": poster.get("template", "esports"), "theme": poster.get("theme", "gold"),
                           "title": poster.get("title", "")}
    name = f"event-poster-{index:02d}.png" if poster else f"promo-image-{index:02d}.png"
    artifact = ctx.save_artifact(
        "image", name, "image/png", data=png, is_demo=route.demo,
        metadata={**extra, "width": width, "height": height,
                  "aspect_ratio": p.get("aspect_ratio", aspect) if poster else aspect, "provider": route.provider,
                  "prompt": prompt,
                  "license": "user-supplied" if own_art else "demo-placeholder" if route.demo else "generated"},
    )
    return {"artifact_id": str(artifact.id), "width": width, "height": height, "provider": route.provider}
