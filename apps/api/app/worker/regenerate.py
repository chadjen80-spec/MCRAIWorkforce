"""Regenerate a file of a finished job (the dashboard's regenerate buttons): images and documents.

Runs on the queue of the agent that makes the file. The current files are replaced only after the
new ones have been generated and stored, so a failure never leaves the job without them.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.agents import document, image, qa, video
from app.agents.base import AgentContext
from app.db import new_session, utcnow
from app.errors import AgentError
from app.models import Artifact, Job, JobStatus, Organization, Project, Task
from app.services.orchestrator import audit, purge_task_artifacts
from app.services.storage import get_storage

log = logging.getLogger("mcr.worker")

# A request older than this is considered abandoned (e.g. the worker restarted) and may be retried.
STALE_AFTER = timedelta(minutes=15)


def is_regenerating(art: Artifact) -> bool:
    meta = art.meta or {}
    since = meta.get("regenerating_since")
    if not since:
        return False
    try:
        from datetime import datetime

        return utcnow() - datetime.fromisoformat(since) < STALE_AFTER
    except ValueError:
        return False


DOCUMENT_KINDS = ("document_pdf", "document_docx")
# Everything the Video agent produces; a new video replaces the whole set so script, subtitles,
# voiceover and MP4 always belong together.
VIDEO_KINDS = ("video", "script", "storyboard", "subtitle", "manifest", "audio")
# kind -> Celery queue of the agent that makes it (only these kinds offer a regenerate button)
# The script, storyboard and subtitles all describe the rendered video (subtitles are its narration, timed to
# the scenes), so a new one always comes with a new video.
REGENERATE_QUEUES = {"image": "image", "document_pdf": "document", "document_docx": "document", "video": "video",
                     "storyboard": "video", "script": "video", "subtitle": "video", "audio": "video"}


def kind_group(kind: str) -> tuple[str, ...]:
    """The files that are regenerated together with a file of this kind."""
    if kind in DOCUMENT_KINDS:
        return DOCUMENT_KINDS
    if kind in VIDEO_KINDS:
        return VIDEO_KINDS
    return (kind,)


def regenerate_artifact(artifact_id: uuid.UUID, guidance: str | None, actor: str) -> None:
    """Make a new version of an image, or of a document (its PDF and DOCX together).

    `guidance` is a new scene description for images, or an extra instruction for documents.
    The current files are replaced only after the new ones are stored.
    """
    session = new_session()
    try:
        old = session.get(Artifact, artifact_id)
        if old is None:
            return
        task = session.get(Task, old.task_id)
        job = session.get(Job, old.job_id)
        assert task is not None and job is not None
        org = session.get(Organization, job.organization_id)
        project = session.get(Project, job.project_id)
        assert org is not None and project is not None
        if job.status in JobStatus.ACTIVE:
            raise AgentError("JOB_ACTIVE", "The job started running again; try once it has finished")

        params = dict(task.params or {})
        if old.kind == "image":
            if guidance:
                params["concept"] = guidance
            replaced = [old]
            run = image.run
        elif old.kind in DOCUMENT_KINDS or old.kind in VIDEO_KINDS:
            # The instruction applies to this regeneration only; a blank one clears an earlier instruction.
            params.pop("instruction", None)
            params.pop("reuse_script", None)
            if old.kind == "audio":
                # A new voiceover reads the same script again; the video is re-timed and re-rendered to it.
                params["reuse_script"] = True
            elif guidance:
                params["instruction"] = guidance
            replaced = list(session.scalars(
                select(Artifact).where(Artifact.task_id == task.id, Artifact.kind.in_(kind_group(old.kind)))))
            run = document.run if old.kind in DOCUMENT_KINDS else video.run
        else:
            raise AgentError("NOT_REGENERATABLE", f"{old.kind} files cannot be regenerated")
        task.params = params
        # The attempt counter is left alone: a regeneration is not a failure retry. Each saved
        # file gets its own storage key, so the current files are untouched until the swap.
        session.commit()
        replaced_ids = {a.id for a in replaced}

        ctx = AgentContext(session=session, job=job, task=task, org=org, project=project)
        try:
            result = run(ctx)
        finally:
            ctx.cleanup()

        wanted = kind_group(old.kind)
        new_files = [a for a in session.scalars(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind.in_(wanted)))
                     if a.id not in replaced_ids]
        if not new_files:
            raise AgentError("NO_OUTPUT", "The agent finished without producing a new file")
        for a in new_files:
            a.meta = {**(a.meta or {}), "regenerated": True}
        task.result = {**(task.result or {}), **result}
        task.params = {k: v for k, v in (task.params or {}).items() if k != "reuse_script"}
        storage = get_storage()
        for a in replaced:  # swap: remove what the new files replace
            try:
                storage.delete(a.storage_key)
            except Exception:  # noqa: BLE001 - an orphaned file must not undo a successful regeneration
                log.warning("could not delete replaced file %s", a.storage_key)
            session.delete(a)
        audit(session, job.organization_id, "artifact.regenerated", actor, job_id=job.id, task_key=task.key,
              detail={"files": sorted(a.filename for a in new_files), "provider": result.get("provider") or task.provider,
                      "guidance": bool(guidance)})
        session.commit()
        log.info("files regenerated", extra={"job_id": str(job.id), "task_key": task.key, "files": len(new_files)})
        rerun_qa(session, job, org, project, actor)
    except Exception as exc:  # noqa: BLE001 - report every failure on the file card
        session.rollback()
        message = exc.message if isinstance(exc, AgentError) else f"{type(exc).__name__}: {exc}"
        code = exc.code if isinstance(exc, AgentError) else "INTERNAL_ERROR"
        log.warning("regeneration failed: %s", message, extra={"artifact_id": str(artifact_id), "error_code": code})
        old = session.get(Artifact, artifact_id)
        if old is not None:
            meta = {k: v for k, v in (old.meta or {}).items() if k != "regenerating_since"}
            old.meta = {**meta, "regenerate_error": f"{code}: {message}"[:500]}
            audit(session, old.organization_id, "artifact.regenerate_failed", actor, job_id=old.job_id,
                  detail={"file": old.filename, "error_code": code})
            session.commit()
    finally:
        session.close()


def regenerate_image(artifact_id: uuid.UUID, concept: str | None, actor: str) -> None:
    """Kept for messages queued before documents could be regenerated."""
    regenerate_artifact(artifact_id, concept, actor)


def rerun_qa(session, job: Job, org: Organization, project: Project, actor: str) -> None:
    """Check the job again after one of its files changed, so the QA verdict describes what is stored now.

    The job is already finished, so a failing check never triggers automatic rework here: anything that
    needs attention is reported as HUMAN_REVIEW. A QA failure never undoes the regeneration.
    """
    qa_task = session.scalar(select(Task).where(Task.job_id == job.id, Task.agent == "qa"))
    if qa_task is None:
        return
    try:
        purge_task_artifacts(session, qa_task)  # the previous QA report described the old files
        ctx = AgentContext(session=session, job=job, task=qa_task, org=org, project=project)
        try:
            report = qa.run(ctx)
        finally:
            ctx.cleanup()
        if report["verdict"] == "REVISE":
            report = {**report, "verdict": "HUMAN_REVIEW", "revise_tasks": [],
                      "summary": report["summary"].split(";")[0] + ". Re-checked after a file was regenerated; review needed."}
        session.refresh(job)
        job.qa_verdict = report["verdict"]
        job.qa_summary = report["summary"]
        job.qa_report = report
        qa_task.result = report
        audit(session, job.organization_id, "qa.verdict", "worker", job_id=job.id, task_key=qa_task.key,
              detail={"verdict": report["verdict"], "summary": report["summary"], "trigger": "file regenerated"})
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        code = exc.code if isinstance(exc, AgentError) else "INTERNAL_ERROR"
        log.warning("QA re-run after regeneration failed: %s", exc, extra={"job_id": str(job.id), "error_code": code})
        audit(session, job.organization_id, "qa.rerun_failed", "worker", job_id=job.id, task_key=qa_task.key,
              detail={"error_code": code, "trigger": "file regenerated"})
        session.commit()


def rerun_qa_for_job(job_id: uuid.UUID, actor: str) -> None:
    session = new_session()
    try:
        job = session.get(Job, job_id)
        if job is None or job.status in JobStatus.ACTIVE:
            return
        org = session.get(Organization, job.organization_id)
        project = session.get(Project, job.project_id)
        assert org is not None and project is not None
        rerun_qa(session, job, org, project, actor)
    finally:
        session.close()


def request_regeneration(session, art: Artifact, guidance: str | None, actor: str) -> None:
    """Validate, mark the file as regenerating and queue the work on its agent's queue."""
    from app.errors import conflict
    from app.worker.celery_app import regenerate_artifact_task

    queue = REGENERATE_QUEUES.get(art.kind)
    if art.kind == "image" and (art.meta or {}).get("artwork") in ("uploaded", "reused", "composed"):
        raise conflict("This poster uses artwork from your library; make a new poster to change it")
    if queue is None:
        raise conflict("Only images, documents and videos can be regenerated here")
    job = session.get(Job, art.job_id)
    if job is not None and job.status in JobStatus.ACTIVE:
        raise conflict("The job is still running. Regenerate its files after it has finished.")
    siblings = list(session.scalars(
        select(Artifact).where(Artifact.task_id == art.task_id, Artifact.kind.in_(kind_group(art.kind)))))
    if any(is_regenerating(a) for a in siblings):
        raise conflict("A new version of this file is already being generated")
    now = utcnow().isoformat()
    for a in siblings:  # files made together are regenerated together (PDF + DOCX; video + script + subtitles…)
        meta = {k: v for k, v in (a.meta or {}).items() if k != "regenerate_error"}
        a.meta = {**meta, "regenerating_since": now}
    task_key = session.scalar(select(Task.key).where(Task.id == art.task_id))
    audit(session, art.organization_id, "artifact.regenerate_requested", actor, job_id=art.job_id, task_key=task_key,
          detail={"file": art.filename, "guidance": bool(guidance)})
    session.commit()
    regenerate_artifact_task.apply_async(args=[str(art.id), guidance, actor], queue=queue)
