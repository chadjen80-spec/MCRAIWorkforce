"""Video Agent: script → storyboard → subtitles → scene manifest → 1080p H.264/AAC MP4 (template-based, FFmpeg)."""
from __future__ import annotations

import io
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents import prompts
from app.agents.base import AgentContext
from app.agents.image import _hex, gradient
from app.errors import AgentError
from app.models import Artifact
from app.services.media import find_fonts, probe_media, run_ffmpeg
from app.services.router import route_llm, route_tts
from app.services.thai_text import draw_text

SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
MAX_DURATION = 120
# Measured speaking speed of the TTS voice, including the natural pauses between sentences:
# Thai ≈ 8.2 characters (without spaces) per second; English ≈ 2.5 words per second.
# The budget keeps ~5% headroom; small overruns are absorbed by a gentle speed-up (below).
TH_CHARS_PER_SEC = 8.2
EN_WORDS_PER_SEC = 2.5
BUDGET_HEADROOM = 0.95
# A voiceover that is at most this much too long is sped up slightly instead of stretching the video.
MAX_SPEEDUP = 1.2


def narration_size(text: str, language: str) -> int:
    """Characters without spaces for Thai, words for English: the unit the budget is expressed in."""
    if language == "th":
        return len(re.sub(r"\s", "", text))
    return len(text.split())


def narration_budget(seconds: float, language: str) -> int:
    rate = TH_CHARS_PER_SEC if language == "th" else EN_WORDS_PER_SEC
    return int(seconds * rate * BUDGET_HEADROOM)


def budget_text(budget: int, language: str) -> str:
    unit = "Thai characters (not counting spaces)" if language == "th" else "words"
    # A floor as well as a ceiling: models tend to write too much, or (once told to be brief) far too little,
    # which leaves long silent stretches in the video.
    return (f"Narration budget: between {int(budget * 0.8)} and {budget} {unit} in total across all scenes, "
            "so the voice fills most of the video without running over.")

# Thai combining marks must stay attached to the preceding consonant when wrapping.
_THAI_COMBINING = set("ัิีึืฺุู็่้๊๋์ํ๎")


class Scene(BaseModel):
    heading: str
    on_screen_text: str
    narration: str
    duration_seconds: float = Field(gt=0)


class VideoScript(BaseModel):
    title: str
    scenes: list[Scene] = Field(min_length=3, max_length=12)


def _demo_script(ctx: AgentContext, duration: int, language: str) -> VideoScript:
    product = ctx.brand["product"]
    brief = (ctx.content_brief() or {}).get("brief") or {}
    msgs = [str(m) for m in (brief.get("key_messages") or [])][:3]
    th = language == "th"
    note = "[DEMO] บทพูดตัวอย่างจากโหมดสาธิต ยังไม่ได้สร้างโดย AI" if th else "[DEMO] Placeholder narration from demo mode, not AI-generated"
    names = (["เปิดเรื่อง", "ปัญหา", "ทางออก", "ประโยชน์", "เชิญชวน"] if th else ["Hook", "Problem", "Solution", "Benefits", "Call to action"])
    weights = [10, 12, 18, 13, 7]
    scenes = [
        Scene(heading=f"[DEMO] {name}", on_screen_text=(msgs[i - 2] if 2 <= i < 2 + len(msgs) else f"{product}"),
              narration=note, duration_seconds=duration * w / sum(weights))
        for i, (name, w) in enumerate(zip(names, weights, strict=True))
    ]
    return VideoScript(title=f"[DEMO] {product}", scenes=scenes)


def normalize_durations(scenes: list[Scene], total: float) -> None:
    current = sum(s.duration_seconds for s in scenes)
    for s in scenes:
        s.duration_seconds = round(s.duration_seconds * total / current, 2)
    scenes[-1].duration_seconds = round(total - sum(s.duration_seconds for s in scenes[:-1]), 2)


# --- text layout -------------------------------------------------------------
def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word wrap that also breaks long unspaced runs (Thai) without splitting combining marks."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}".strip()
            if font.getlength(candidate) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            chunk = ""
            for ch in word:
                if chunk and ch not in _THAI_COMBINING and font.getlength(chunk + ch) > max_width:
                    lines.append(chunk)
                    chunk = ""
                chunk += ch
            line = chunk
        if line:
            lines.append(line)
    return lines


def _draw_block(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, x: int, y: int, max_width: int,
                fill, line_gap: float = 1.35, max_lines: int = 6) -> int:
    lines = wrap_text(text, font, max_width)[:max_lines]
    step = int(font.size * line_gap)
    for i, line in enumerate(lines):
        draw_text(draw, (x, y + i * step), line, font, fill)
    return y + len(lines) * step


def render_scene(scene: Scene, index: int, total: int, size: tuple[int, int], brand: dict, background: Image.Image | None,
                 *, demo: bool) -> Image.Image:
    w, h = size
    if background is not None:
        bg = background.convert("RGB")
        scale = max(w / bg.width, h / bg.height)
        bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1))
        left, top = (bg.width - w) // 2, (bg.height - h) // 2
        canvas = bg.crop((left, top, left + w, top + h)).filter(ImageFilter.GaussianBlur(3))
        canvas = Image.blend(canvas, Image.new("RGB", size, _hex(brand["background_color"])), 0.62)
    else:
        canvas = gradient(size, brand["background_color"], "#0F2A5F")
    draw = ImageDraw.Draw(canvas, "RGBA")
    regular, bold = find_fonts()
    unit = min(w, h)
    margin = int(unit * 0.09)
    f_head = ImageFont.truetype(bold, int(unit * 0.045))
    f_main = ImageFont.truetype(bold, int(unit * 0.085))
    f_sub = ImageFont.truetype(regular, int(unit * 0.036))
    f_small = ImageFont.truetype(regular, int(unit * 0.026))
    accent, white = (*_hex(brand["accent_color"]), 255), (255, 255, 255, 255)

    draw.rectangle((margin, margin, margin + int(unit * 0.09), margin + int(unit * 0.012)), fill=accent)
    y = _draw_block(draw, scene.heading, f_head, margin, margin + int(unit * 0.04), w - 2 * margin, accent, max_lines=2)
    _draw_block(draw, scene.on_screen_text, f_main, margin, y + int(unit * 0.05), w - 2 * margin, white, max_lines=5)

    # subtitle band (burned-in captions; an .srt sidecar is produced as well)
    sub_lines = wrap_text(scene.narration, f_sub, w - 2 * margin - 40)[:3]
    if sub_lines:
        step = int(f_sub.size * 1.4)
        band_h = step * len(sub_lines) + 40
        band_top = h - margin - int(unit * 0.06) - band_h
        draw.rounded_rectangle((margin, band_top, w - margin, band_top + band_h), radius=18, fill=(0, 0, 0, 150))
        for i, line in enumerate(sub_lines):
            draw_text(draw, (margin + 20, band_top + 20 + i * step), line, f_sub, (241, 245, 249, 255))

    draw.text((margin, h - margin), f"{brand['product']} · {brand['company']}", font=f_small, fill=(203, 213, 225, 230), anchor="ls")
    draw.text((w - margin, h - margin), f"{index}/{total}", font=f_small, fill=(203, 213, 225, 230), anchor="rs")
    progress = int((w - 2 * margin) * index / total)
    draw.rectangle((margin, h - margin + 14, margin + progress, h - margin + 20), fill=accent)
    if demo:
        draw.text((w - margin, margin), "DEMO", font=f_head, fill=(248, 113, 113, 255), anchor="ra")
    return canvas


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def build_srt(scenes: list[Scene]) -> str:
    out, t = [], 0.0
    for i, s in enumerate(scenes, 1):
        out.append(f"{i}\n{_ts(t)} --> {_ts(t + s.duration_seconds)}\n{s.narration.strip()}\n")
        t += s.duration_seconds
    return "\n".join(out)


SCENE_GAP = 0.35  # silence after each clip, so sentences don't run into the next slide


def script_size(script: VideoScript, language: str) -> int:
    return narration_size(" ".join(s.narration for s in script.scenes), language)


def clip_length(clip: Path | None) -> float:
    return float(probe_media(clip).get("duration_seconds") or 0) if clip else 0.0


def shorten_script(ctx: AgentContext, client: Any, script: VideoScript, budget: int, language: str) -> VideoScript:
    """One model pass that cuts the narration to `budget`, with an explicit limit for every scene.
    Returns the original script if the model's answer is not actually shorter."""
    current = script_size(script, language)
    unit = "Thai characters (not counting spaces)" if language == "th" else "words"
    limits = [max(4, int(narration_size(s.narration, language) * budget / max(current, 1))) for s in script.scenes]
    per_scene = "; ".join(f"scene {i}: at most {n}" for i, n in enumerate(limits, 1))
    request = (
        f"This video script's narration is {current} {unit} long but must be at most {budget} in total. "
        f"Rewrite each scene's narration within its limit ({per_scene} {unit}). Keep the meaning, the number of scenes, "
        "the headings, on-screen text and durations; drop filler words and repeated product names. Return the whole script.\n\n"
        + script.model_dump_json()
    )
    shorter, usage = client.complete_json(prompts.VIDEO, request, VideoScript, temperature=0.3)
    ctx.record_usage("openai", "chat", {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}, usage.cost_usd)
    if len(shorter.scenes) != len(script.scenes) or script_size(shorter, language) >= current:
        return script
    for new, old in zip(shorter.scenes, script.scenes, strict=True):
        new.duration_seconds = old.duration_seconds
    return shorter


def synthesize_scene_clips(ctx: AgentContext, tts: Any, scenes: list[Scene], work: Path) -> list[Path | None]:
    """One TTS clip per scene (None for scenes without narration). Raises AgentError on provider failure."""
    clips: list[Path | None] = []
    for i, scene in enumerate(scenes, 1):
        text = scene.narration.strip()
        if not text:
            clips.append(None)
            continue
        audio = tts.openai.synthesize_speech(text)
        raw = work / f"voice_{i:02d}_raw.mp3"
        raw.write_bytes(audio)
        # TTS clips start and end with a little silence; trimmed, the voice starts right on the slide change
        # and the clips' padding no longer adds up across scenes.
        clip = work / f"voice_{i:02d}.mp3"
        trim = "silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.05"
        run_ffmpeg(["-y", "-i", str(raw), "-af", f"{trim},areverse,{trim},areverse", "-c:a", "libmp3lame", "-b:a", "128k",
                    str(clip)])
        clips.append(clip)
        cost = ctx.settings.tts_usd_per_1k_chars * Decimal(len(text)) / Decimal(1000)
        ctx.record_usage("openai", "tts", {"characters": len(text), "scene": i}, cost)
        ctx.heartbeat()
    return clips


def fit_scenes_to_voice(scenes: list[Scene], clips: list[Path | None], target: float, work: Path,
                        limitations: list[str]) -> list[float]:
    """Set every scene's duration from its own clip, keeping the total at `target` when possible.

    Spare time is shared out in proportion to the planned scene lengths. If the clips are too long,
    they are all sped up by the same factor (at most MAX_SPEEDUP, pitch kept); only beyond that is
    the video lengthened. Returns the (possibly sped-up) clip lengths.
    """
    lengths = [float(probe_media(c).get("duration_seconds") or 0) if c else 0.0 for c in clips]
    need = sum(length + SCENE_GAP for length in lengths)
    if need > target:
        factor = need / target
        if factor <= MAX_SPEEDUP:
            for i, clip in enumerate(clips):
                if clip is None:
                    continue
                faster = work / f"voice_{i + 1:02d}_fit.mp3"
                run_ffmpeg(["-y", "-i", str(clip), "-filter:a", f"atempo={factor:.4f}", "-c:a", "libmp3lame", "-b:a", "128k",
                            str(faster)])
                clips[i] = faster
                lengths[i] = float(probe_media(faster).get("duration_seconds") or lengths[i] / factor)
            if factor > 1.1:  # noticeable: let QA ask a human to listen
                limitations.append(f"voice_sped_up: voiceover played {factor:.2f}x faster to fit {target:.0f}s")
            need = sum(length + SCENE_GAP for length in lengths)
    planned = [s.duration_seconds for s in scenes]
    spare = target - need
    min_gap = 0.1
    if spare < 0 and -spare <= len(scenes) * (SCENE_GAP - min_gap):
        # A fraction of a second over: take it out of the pauses between scenes rather than lengthening the video.
        gap = SCENE_GAP + spare / len(scenes)
        for scene, length in zip(scenes, lengths, strict=True):
            scene.duration_seconds = round(length + gap, 3)
        scenes[-1].duration_seconds = round(target - sum(s.duration_seconds for s in scenes[:-1]), 3)
        return lengths
    if spare >= 0:
        weight = sum(planned) or 1.0
        for scene, length, plan in zip(scenes, lengths, planned, strict=True):
            scene.duration_seconds = round(length + SCENE_GAP + spare * plan / weight, 3)
        scenes[-1].duration_seconds = round(target - sum(s.duration_seconds for s in scenes[:-1]), 3)
    else:
        # Never cut narration off: every scene gets exactly the time its sentence needs.
        for scene, length in zip(scenes, lengths, strict=True):
            scene.duration_seconds = round(max(length + SCENE_GAP, 1.5), 3)
        total = sum(s.duration_seconds for s in scenes)
        if total > MAX_DURATION:
            normalize_durations(scenes, float(MAX_DURATION))
            total = float(MAX_DURATION)
        limitations.append(f"duration_extended: narration needs {need:.1f}s, video extended to {total:.0f}s")
    return lengths


def assemble_voice_track(scenes: list[Scene], clips: list[Path | None], lengths: list[float], work: Path) -> Path:
    """One audio track where scene i's clip starts exactly when scene i starts, padded with silence."""
    parts = []
    for i, (scene, clip) in enumerate(zip(scenes, clips, strict=True), 1):
        part = work / f"voice_part_{i:02d}.wav"
        dur = f"{scene.duration_seconds:.3f}"
        if clip is None:
            run_ffmpeg(["-y", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-t", dur, str(part)])
        else:
            run_ffmpeg(["-y", "-i", str(clip), "-af", "apad", "-t", dur, "-ar", "44100", "-ac", "2", str(part)])
        parts.append(f"file '{part.name}'")
    (work / "voice_parts.txt").write_text("\n".join(parts) + "\n", encoding="utf-8")
    track = work / "voiceover.mp3"
    run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(work / "voice_parts.txt"), "-c:a", "libmp3lame", "-b:a", "128k",
                str(track)])
    return track


def _current_script(ctx: AgentContext) -> tuple[VideoScript, bool] | None:
    """The script this task saved last (for re-recording the voiceover), with its demo flag."""
    art = ctx.session.scalars(
        select(Artifact).where(Artifact.task_id == ctx.task.id, Artifact.kind == "script")
        .order_by(Artifact.created_at.desc())).first()
    if art is None:
        return None
    try:
        return VideoScript.model_validate_json(ctx.storage.read_bytes(art.storage_key)), art.is_demo
    except (OSError, ValueError):
        return None


def run(ctx: AgentContext) -> dict[str, Any]:
    p = ctx.task.params
    target = int(p.get("duration_seconds", 60))
    aspect = p.get("aspect_ratio", "16:9")
    language = p.get("language", "th")
    size = SIZES.get(aspect, SIZES["16:9"])
    brand = ctx.brand
    limitations: list[str] = []

    # 1) script ----------------------------------------------------------------
    # Each scene ends with a short pause (SCENE_GAP); budget the narration for the time that is left.
    budget = narration_budget(target - max(3, target // 6) * SCENE_GAP, language)
    route = route_llm(ctx.session, ctx.job, ctx.org)
    assert route is not None
    client = route.openai
    script_demo = route.demo or client is None
    previous = _current_script(ctx) if p.get("reuse_script") else None
    if previous is not None:
        # "New voiceover": read the same script again; only the recording (and the timing) changes.
        script, script_demo = previous
    elif client is None or script_demo:
        script = _demo_script(ctx, target, language)
    else:
        brief = ctx.content_brief()
        user = (
            f"{prompts.context_block(ctx.business_context, brand)}\n\n"
            f"Campaign brief from the Content agent: {(brief or {}).get('brief', 'not available')}\n"
            f"Request from the business owner:\n\"\"\"\n{ctx.job.command}\n\"\"\"\n\n"
            f"Language: {'Thai' if language == 'th' else 'English'}. Total duration: {target} seconds. "
            f"{max(3, min(7, target // 8))}-{max(4, min(7, target // 6))} scenes. {budget_text(budget, language)}"
            + (f"\nAdditional instruction from the business owner for this version: {p['instruction']}"
               if p.get("instruction") else "")
        )
        script, usage = client.complete_json(prompts.VIDEO, user, VideoScript)
        ctx.record_usage("openai", "chat", {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                         usage.cost_usd)
        # Models routinely overshoot length limits, and a single "make it shorter" often trims too little.
        for _ in range(2):
            if script_size(script, language) <= budget * 1.1:
                break
            script = shorten_script(ctx, client, script, budget, language)
    normalize_durations(script.scenes, float(target))
    ctx.heartbeat()

    # 2) voiceover: one clip per scene, so each scene starts with its own narration ---------
    work = ctx.workdir
    narration = " ".join(s.narration.strip() for s in script.scenes)
    voice_path: Path | None = None
    voice_provider = None
    if script_demo:
        limitations.append("voiceover_skipped: script is demo placeholder text, so no voiceover was synthesized")
    else:
        tts = route_tts(ctx.session, ctx.job, ctx.org, len(narration))
        if tts is None or tts.openai is None:
            limitations.append("voiceover_unavailable: no text-to-speech provider is configured, authorized or within budget; "
                               "the video has a silent audio track")
        else:
            try:
                clips = synthesize_scene_clips(ctx, tts, script.scenes, work)
            except AgentError as exc:
                limitations.append(f"voiceover_failed: {exc.code}: {exc.message[:200]}; the video has a silent audio track")
            else:
                voice_provider = "openai"
                need = sum(clip_length(c) + SCENE_GAP for c in clips)
                if need > target * MAX_SPEEDUP and client is not None:
                    # Still too long even for a gentle speed-up: cut the narration in proportion to how
                    # much it overran, then record the clips again (TTS is cheap; a stretched video is not).
                    tighter = int(script_size(script, language) * (target * 1.05) / need)
                    retry = shorten_script(ctx, client, script, tighter, language)
                    if script_size(retry, language) < script_size(script, language):
                        for old, new in zip(script.scenes, retry.scenes, strict=False):
                            old.narration = new.narration
                        try:
                            clips = synthesize_scene_clips(ctx, tts, script.scenes, work)
                        except AgentError as exc:
                            limitations.append(f"voiceover_retry_failed: {exc.code}; kept the first recording")
                lengths = fit_scenes_to_voice(script.scenes, clips, float(target), work, limitations)
                voice_path = assemble_voice_track(script.scenes, clips, lengths, work)
    total = round(sum(s.duration_seconds for s in script.scenes), 2)

    ctx.use_route(type(route)(
        provider="ffmpeg", location="local", demo=script_demo,
        reason=f"local FFmpeg render; script: {route.reason}; voiceover: {voice_provider or 'none'}",
    ))

    # 3) scenes --------------------------------------------------------------------
    backgrounds: list[tuple[str, Image.Image]] = []
    for dep in ctx.dependencies().values():
        for art in dep.artifacts:
            if art.kind == "image" and not art.is_demo:
                with Image.open(io.BytesIO(ctx.storage.read_bytes(art.storage_key))) as im:
                    backgrounds.append((str(art.id), im.convert("RGB").copy()))
    assets_used = []
    n = len(script.scenes)
    concat = []
    for i, scene in enumerate(script.scenes, 1):
        bg_id, bg = backgrounds[(i - 1) % len(backgrounds)] if backgrounds else (None, None)
        frame = render_scene(scene, i, n, size, brand, bg, demo=script_demo)
        name = f"scene_{i:02d}.png"
        frame.save(work / name, "PNG")
        concat.append(f"file '{name}'\nduration {scene.duration_seconds:.3f}")
        assets_used.append({"scene": i, "background_artifact_id": bg_id,
                            "background": "generated image (this job)" if bg_id else "procedural gradient"})
        if i % 3 == 0:
            ctx.heartbeat()
    concat.append(f"file 'scene_{n:02d}.png'")  # concat demuxer needs the last file repeated
    (work / "scenes.txt").write_text("\n".join(concat) + "\n", encoding="utf-8")

    # 4) render ----------------------------------------------------------------------
    fps = ctx.settings.video_fps
    out = work / "promo.mp4"
    args = ["-y", "-f", "concat", "-safe", "0", "-i", str(work / "scenes.txt")]
    if voice_path:
        args += ["-i", str(voice_path), "-af", "apad"]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    args += ["-vf", f"fps={fps},scale={size[0]}:{size[1]},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "20", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-t", f"{total:.3f}",
             "-movflags", "+faststart", str(out)]
    run_ffmpeg(args)
    ctx.heartbeat()
    info = probe_media(out)
    if not out.is_file() or out.stat().st_size == 0 or info.get("video_codec") != "h264":
        raise AgentError("VIDEO_RENDER_INVALID", f"Rendered file failed verification: {info}", retryable=True)

    # 5) artifacts ----------------------------------------------------------------------
    base_meta = {"language": language, "provider": "ffmpeg", "script_provider": route.provider,
                 "license": "generated", "limitations": limitations}
    script_json = json.dumps(script.model_dump(), ensure_ascii=False, indent=2)
    ctx.save_artifact("script", "video-script.json", "application/json", data=script_json.encode("utf-8"),
                      is_demo=script_demo, metadata=base_meta,
                      text_preview="\n\n".join(f"[{s.heading}] {s.narration}" for s in script.scenes))
    t = 0.0
    board = [f"# Storyboard — {script.title}\n"]
    for i, s in enumerate(script.scenes, 1):
        board.append(f"## Scene {i} ({t:.1f}s – {t + s.duration_seconds:.1f}s): {s.heading}\n\n"
                     f"- On screen: {s.on_screen_text}\n- Narration: {s.narration}\n- Visual: {assets_used[i - 1]['background']}\n")
        t += s.duration_seconds
    storyboard = "\n".join(board)
    ctx.save_artifact("storyboard", "storyboard.md", "text/markdown; charset=utf-8", data=storyboard.encode("utf-8"),
                      is_demo=script_demo, metadata=base_meta, text_preview=storyboard)
    srt = build_srt(script.scenes)
    ctx.save_artifact("subtitle", "subtitles.srt", "application/x-subrip", data=srt.encode("utf-8"),
                      is_demo=script_demo, metadata=base_meta, text_preview=srt)
    manifest = {"title": script.title, "aspect_ratio": aspect, "width": size[0], "height": size[1], "fps": fps,
                "duration_seconds": total, "voiceover": voice_provider, "assets": assets_used,
                "asset_licensing": "All visuals are generated within this job or drawn procedurally; no third-party media.",
                "scenes": [s.model_dump() for s in script.scenes], "limitations": limitations}
    ctx.save_artifact("manifest", "scene-manifest.json", "application/json", is_demo=script_demo, metadata=base_meta,
                      data=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    if voice_path:
        ctx.save_artifact("audio", "voiceover.mp3", "audio/mpeg", path=voice_path, metadata=base_meta)
    ctx.save_artifact("video", "promo-video.mp4", "video/mp4", path=out, is_demo=script_demo,
                      metadata={**base_meta, **info, "aspect_ratio": aspect, "target_duration_seconds": target,
                                "expected_duration_seconds": total, "has_voiceover": bool(voice_path)})
    return {"duration_seconds": info.get("duration_seconds"), "expected_duration_seconds": total,
            "has_voiceover": bool(voice_path), "limitations": limitations, "scenes": n, **info}
