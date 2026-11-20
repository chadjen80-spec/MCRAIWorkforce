"""QA Agent: deterministic integrity checks on every deliverable + optional LLM text review.

Verdicts: PASS · REVISE (bounded automatic rework) · HUMAN_REVIEW (escalation).
Check status: pass | warn (needs human judgement) | fail (deliverable is not acceptable).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents import prompts
from app.agents.base import AgentContext, has_thai
from app.errors import AgentError
from app.models import Artifact, Task, TaskStatus
from app.services.media import probe_media
from app.services.router import route_llm

EXPECTED_VIDEO = {"16:9": (1920, 1080), "9:16": (1080, 1920)}


class TextIssue(BaseModel):
    where: str
    issue: str


class TextReview(BaseModel):
    issues: list[TextIssue] = Field(default_factory=list)


class Checker:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(self, name: str, status: Literal["pass", "warn", "fail"], detail: str, task_key: str | None = None,
            revisable: bool = False) -> None:
        self.checks.append({"name": name, "status": status, "detail": detail, "task_key": task_key,
                            "revisable": revisable and status == "fail"})


def _verify_stored(ctx: AgentContext, art: Artifact, c: Checker, task_key: str) -> bytes | None:
    name = f"file:{art.filename}"
    if not ctx.storage.exists(art.storage_key):
        c.add(name, "fail", "file is missing from storage", task_key, True)
        return None
    data = ctx.storage.read_bytes(art.storage_key)
    if len(data) == 0 or len(data) != art.size_bytes:
        c.add(name, "fail", f"size mismatch (stored {len(data)} bytes, recorded {art.size_bytes})", task_key, True)
        return None
    if hashlib.sha256(data).hexdigest() != art.sha256:
        c.add(name, "fail", "checksum mismatch (file is corrupt or was altered)", task_key, True)
        return None
    return data


def _check_content(task: Task, arts: list[Artifact], blobs: dict, c: Checker, business_context: str = "") -> None:
    wanted = int(task.params.get("caption_count", 0))
    captions = [a for a in arts if a.kind == "caption"]
    ok = len(captions) == wanted
    c.add("content:caption_count", "pass" if ok else "fail", f"{len(captions)} of {wanted} captions", task.key, True)
    lang = task.params.get("caption_language", "th")
    texts = [blobs[a.id].decode("utf-8", "replace") for a in captions if a.id in blobs]
    if texts:
        empty = [i for i, t in enumerate(texts, 1) if len(t.strip()) < 10]
        if empty:
            c.add("content:caption_text", "fail", f"captions {empty} are empty or too short", task.key, True)
        if lang == "th":
            wrong = [i for i, t in enumerate(texts, 1) if not has_thai(t)]
            c.add("content:language", "fail" if wrong else "pass",
                  f"captions {wrong} are not Thai" if wrong else "captions are in Thai", task.key, True)
        if len(set(texts)) < len(texts):
            c.add("content:duplicates", "warn", "some captions are identical", task.key)
        # Natural-writing checks (clichés, similarity between captions, repetition, unsupported claims, emoji and
        # hashtag overuse). They need a person's judgement, so they are warnings, never automatic rework loops.
        if not any(a.is_demo for a in captions):
            from app.agents.captions import CreativeBrief, issues_for

            saved = (task.result or {}).get("creative_brief")
            try:
                brief = CreativeBrief.model_validate(saved) if saved else None
            except ValueError:
                brief = None
            if brief is not None:
                found = issues_for(texts, brief, business_context)
                for i, issues in enumerate(found, 1):
                    for x in issues:
                        if x["code"] in ("language", "unsupported_claim"):
                            continue  # reported by the checks above / the claim flags
                        c.add(f"content:{x['code']}", "warn", f"caption {i}: {x['detail']}", task.key)
                if not any(found):
                    c.add("content:natural_writing", "pass", "no clichés, repetition or near-duplicate captions found", task.key)
    for flag in (task.result or {}).get("claim_flags", []):
        c.add("content:unsupported_claim", "warn", flag, task.key)


def _check_image(task: Task, arts: list[Artifact], blobs: dict, c: Checker) -> None:
    images = [a for a in arts if a.kind == "image"]
    if len(images) != 1:
        c.add("image:file", "fail", f"expected 1 image file, found {len(images)}", task.key, True)
        return
    art = images[0]
    if art.id not in blobs:
        return
    try:
        with Image.open(io.BytesIO(blobs[art.id])) as im:
            im.verify()
        with Image.open(io.BytesIO(blobs[art.id])) as im:
            w, h = im.size
    except Exception as exc:  # noqa: BLE001
        c.add("image:decodable", "fail", f"{art.filename} cannot be decoded: {exc}", task.key, True)
        return
    c.add("image:decodable", "pass", f"{art.filename} is a valid {w}x{h} image", task.key)
    aspect = task.params.get("aspect_ratio", "16:9")
    aw, ah = (int(x) for x in aspect.split(":"))
    if abs(w / h - aw / ah) > 0.12 * (aw / ah):
        c.add("image:aspect_ratio", "warn", f"{art.filename} is {w}x{h}, requested {aspect}", task.key)
    if min(w, h) < 512:
        c.add("image:resolution", "warn", f"{art.filename} is low resolution ({w}x{h})", task.key)
    if not art.meta.get("license"):
        c.add("image:license", "warn", f"{art.filename} has no licensing metadata", task.key)
    if task.params.get("poster"):
        if missing := art.meta.get("missing_logos"):
            c.add("poster:logos", "warn", f"{art.filename}: {missing} logo(s) were deleted from the library and left out",
                  task.key)
        else:
            c.add("poster:layout", "pass", f"{art.filename}: text and logos typeset on the artwork", task.key)


def _check_document(task: Task, arts: list[Artifact], blobs: dict, c: Checker) -> None:
    for fmt in task.params.get("formats") or ["pdf", "docx"]:
        kind = f"document_{fmt}"
        found = [a for a in arts if a.kind == kind]
        if not found:
            c.add(f"document:{fmt}", "fail", f"{fmt.upper()} file was not generated", task.key, True)
            continue
        art = found[0]
        if art.id not in blobs:
            continue
        data = blobs[art.id]
        if fmt == "pdf":
            pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
            ok = data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:] and pages >= 1
            c.add("document:pdf", "pass" if ok else "fail",
                  f"valid PDF, {pages} page(s)" if ok else "PDF structure is invalid", task.key, True)
            if ok and b"/FontFile2" not in data:
                c.add("document:pdf_fonts", "warn", "PDF has no embedded TrueType font; Thai text may not render", task.key)
        else:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    ok = z.testzip() is None and "word/document.xml" in z.namelist()
                    body = z.read("word/document.xml") if ok else b""
            except zipfile.BadZipFile:
                ok, body = False, b""
            c.add("document:docx", "pass" if ok and len(body) > 500 else "fail",
                  "valid DOCX" if ok else "DOCX container is invalid", task.key, True)


def _check_video(ctx: AgentContext, task: Task, arts: list[Artifact], c: Checker) -> None:
    videos = [a for a in arts if a.kind == "video"]
    if len(videos) != 1:
        c.add("video:file", "fail", f"expected 1 MP4, found {len(videos)}", task.key, True)
        return
    art = videos[0]
    if not ctx.storage.exists(art.storage_key):
        return  # already reported by the storage check
    local = ctx.workdir / f"qa-{art.id}.mp4"
    ctx.storage.download_to(art.storage_key, local)
    info = probe_media(local)
    codecs_ok = info.get("video_codec") == "h264" and info.get("audio_codec") == "aac"
    c.add("video:codecs", "pass" if codecs_ok else "fail",
          f"video={info.get('video_codec')}, audio={info.get('audio_codec')} (required h264/aac)", task.key, True)
    want = EXPECTED_VIDEO.get(task.params.get("aspect_ratio", "16:9"), (1920, 1080))
    res_ok = (info.get("width"), info.get("height")) == want
    c.add("video:resolution", "pass" if res_ok else "fail",
          f"{info.get('width')}x{info.get('height')} (required {want[0]}x{want[1]})", task.key, True)
    expected = float(art.meta.get("expected_duration_seconds") or task.params.get("duration_seconds", 60))
    actual = info.get("duration_seconds") or 0
    dur_ok = abs(actual - expected) <= 1.5
    c.add("video:duration", "pass" if dur_ok else "fail", f"{actual:.1f}s (expected {expected:.1f}s)", task.key, True)
    for kind in ("subtitle", "script", "manifest"):
        if not any(a.kind == kind for a in arts):
            c.add(f"video:{kind}", "fail", f"{kind} file was not generated", task.key, True)
    for lim in art.meta.get("limitations") or []:
        c.add("video:limitation", "warn", lim, task.key)
    if not art.meta.get("license"):
        c.add("video:license", "warn", "video has no licensing metadata", task.key)


def _llm_review(ctx: AgentContext, texts: list[str], c: Checker) -> None:
    route = route_llm(ctx.session, ctx.job, ctx.org, required=False)
    if not route or not route.openai or not texts:
        c.add("qa:text_review", "pass", "deterministic checks only (no cloud LLM available for semantic review)")
        return
    ctx.use_route(route)
    numbered = "\n".join(f"[caption {i}] {t}" for i, t in enumerate(texts, 1))
    try:
        review, usage = route.openai.complete_json(
            prompts.QA, f"{prompts.context_block(ctx.business_context, ctx.brand)}\n\nTexts to review:\n{numbered}",
            TextReview, temperature=0)
    except AgentError as exc:
        c.add("qa:text_review", "warn", f"LLM review unavailable ({exc.code}); only deterministic checks ran")
        return
    ctx.record_usage("openai", "chat", {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                     usage.cost_usd)
    if not review.issues:
        c.add("qa:text_review", "pass", "LLM review found no issues")
    for issue in review.issues[:20]:
        c.add("qa:text_review", "warn", f"{issue.where}: {issue.issue}")


def run(ctx: AgentContext) -> dict[str, Any]:
    c = Checker()
    tasks = ctx.session.scalars(
        select(Task).where(Task.job_id == ctx.job.id, Task.agent.notin_(["planner", "qa"])).order_by(Task.key)
    ).all()
    caption_texts: list[str] = []
    demo_count = 0

    for task in tasks:
        if task.status != TaskStatus.SUCCEEDED:
            c.add(f"task:{task.key}", "fail",
                  f"{task.title or task.key} did not complete ({task.status}: {task.error_code or 'no error code'})", task.key)
            continue
        arts = list(ctx.session.scalars(select(Artifact).where(Artifact.task_id == task.id)))
        if not arts:
            c.add(f"task:{task.key}", "fail", "task reported success but produced no files", task.key, True)
            continue
        blobs: dict[Any, bytes] = {}
        intact = True
        for art in arts:
            demo_count += int(art.is_demo)
            if art.kind == "video":  # verified via probe to avoid loading large files in memory twice
                if not ctx.storage.exists(art.storage_key) or ctx.storage.size(art.storage_key) != art.size_bytes:
                    c.add(f"file:{art.filename}", "fail", "file is missing or truncated in storage", task.key, True)
                    intact = False
                continue
            data = _verify_stored(ctx, art, c, task.key)
            if data is None:
                intact = False
            else:
                blobs[art.id] = data
        if intact:
            c.add(f"task:{task.key}", "pass", f"{len(arts)} file(s) stored and checksums verified", task.key)
        if task.agent == "content":
            _check_content(task, arts, blobs, c, ctx.business_context)
            caption_texts += [blobs[a.id].decode("utf-8", "replace") for a in arts if a.kind == "caption" and a.id in blobs and not a.is_demo]
        elif task.agent == "image":
            _check_image(task, arts, blobs, c)
        elif task.agent == "document":
            _check_document(task, arts, blobs, c)
        elif task.agent == "video":
            _check_video(ctx, task, arts, c)
        ctx.heartbeat()

    if demo_count:
        c.add("qa:demo_output", "warn",
              f"{demo_count} file(s) are DEMO placeholders, not AI-generated deliverables. Not suitable for publication.")
    _llm_review(ctx, caption_texts, c)
    if ctx.task.provider is None:
        from app.services.router import Route

        ctx.use_route(Route("local_cpu", "local", "deterministic validation on local CPU"))

    fails = [x for x in c.checks if x["status"] == "fail"]
    warns = [x for x in c.checks if x["status"] == "warn"]
    revise = sorted({x["task_key"] for x in fails if x["revisable"] and x["task_key"]})
    can_revise = bool(revise) and ctx.job.qa_revisions < ctx.settings.qa_max_revisions
    verdict: str
    if can_revise:
        verdict = "REVISE"
        summary = f"{len(fails)} check(s) failed; reworking {', '.join(revise)} (revision {ctx.job.qa_revisions + 1})."
    elif fails:
        verdict = "HUMAN_REVIEW"
        summary = f"{len(fails)} check(s) failed and cannot be fixed automatically: " + "; ".join(x["detail"] for x in fails[:4])
    elif warns:
        verdict = "HUMAN_REVIEW"
        summary = f"All files verified. {len(warns)} item(s) need human review: " + "; ".join(x["detail"] for x in warns[:4])
    else:
        verdict = "PASS"
        summary = f"All {len(c.checks)} checks passed."

    report = {
        "verdict": verdict, "summary": summary, "checks": c.checks,
        "revise_tasks": revise if can_revise else [],
        # deliverables that reported success but failed verification and cannot be reworked any more
        "reject_tasks": [] if can_revise else sorted({x["task_key"] for x in fails if x["revisable"] and x["task_key"]}),
    }
    ctx.save_artifact("qa_report", f"qa-report-r{ctx.job.qa_revisions}.json", "application/json",
                      data=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"), text_preview=summary)
    return report
