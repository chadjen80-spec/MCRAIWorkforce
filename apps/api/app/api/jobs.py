from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import schemas
from app.api.assets import check_poster_assets
from app.db import get_db
from app.deps import Principal, get_principal, require_admin
from app.errors import AppError, not_found
from app.models import (
    Artifact,
    AuditLog,
    Job,
    JobStatus,
    Project,
    Task,
    TaskDependency,
    UsageEvent,
)
from app.security import verify_artifact_signature
from app.services import orchestrator
from app.services.budget import committed_this_month
from app.services.router import month_spend
from app.services.storage import get_storage
from app.services.workers import queue_status

router = APIRouter(prefix="/v1", tags=["jobs"])
public_router = APIRouter(prefix="/v1", tags=["artifacts"])


def _job(session: Session, principal: Principal, job_id: uuid.UUID) -> Job:
    # Tenant isolation: always filter by the caller's organization; foreign ids look like 404s.
    job = session.scalar(select(Job).where(Job.id == job_id, Job.organization_id == principal.organization.id))
    if job is None:
        raise not_found("Job")
    return job


def _counts(session: Session, job_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    out: dict[uuid.UUID, dict[str, int]] = {j: {} for j in job_ids}
    if job_ids:
        rows = session.execute(
            select(Task.job_id, Task.status, func.count()).where(Task.job_id.in_(job_ids)).group_by(Task.job_id, Task.status))
        for job_id, status, n in rows:
            out[job_id][status] = n
    return out


def _job_out(session: Session, job: Job, *, detail: bool = False) -> schemas.JobOut:
    session.refresh(job)
    return schemas.job_out(job, _counts(session, [job.id])[job.id], detail=detail)


def _artifacts_out(session: Session, arts: list[Artifact], principal: Principal) -> list[schemas.ArtifactOut]:
    """Files with their release state. People (dashboard) always get a link so they can review drafts;
    automations (API keys, e.g. n8n) get a link only once the job is released, so nothing unapproved can
    be delivered automatically."""
    task_ids = {a.task_id for a in arts}
    tasks = {t.id: t for t in session.scalars(select(Task).where(Task.id.in_(task_ids)))} if task_ids else {}
    job_ids = {a.job_id for a in arts}
    status = dict(session.execute(select(Job.id, Job.status).where(Job.id.in_(job_ids))).all()) if job_ids else {}
    out = []
    for a in arts:
        released = status.get(a.job_id) in schemas.RELEASED_JOB_STATUSES
        out.append(schemas.artifact_out(a, tasks[a.task_id].key, tasks[a.task_id].agent, released=released,
                                        reviewer=principal.user is not None))
    return out


@router.post("/jobs", response_model=schemas.JobOut, status_code=202, summary="Submit a master command")
def create_job(
    body: schemas.JobCreate,
    response: Response,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    org = principal.organization
    if body.project_id:
        project = session.scalar(select(Project).where(Project.id == body.project_id, Project.organization_id == org.id))
        if project is None:
            raise not_found("Project")
    else:
        project = orchestrator.default_project(session, org)
    budget = body.budget_limit_usd or org.default_job_budget_usd
    if committed_this_month(session, org.id) >= org.monthly_budget_usd > 0:
        raise AppError(402, "BUDGET_EXCEEDED", "The organization's monthly budget is exhausted")
    agents = list(body.agents or schemas.ALL_AGENTS)
    aspect: str = body.aspect_ratio
    options = None
    if body.poster is not None:
        # A poster job makes only the poster(s): portrait artwork with the text and logos typeset on top.
        check_poster_assets(session, org.id, body.poster)
        agents, aspect = ["image"], "4:5" if body.poster.template == "tournament" else "9:16"
        options = {"poster": body.poster.model_dump(mode="json")}
    job, created = orchestrator.create_job(
        session, org, command=body.command, agents=agents, options=options,
        budget_limit_usd=Decimal(budget), language=body.language, aspect_ratio=aspect,
        require_approval=body.require_approval, data_classification=body.data_classification, project=project,
        auto_start=body.auto_start, idempotency_key=idempotency_key or None,
        user_id=principal.user.id if principal.user else None, actor=principal.actor,
    )
    if not created:
        response.status_code = 200
    return _job_out(session, job)


@router.get("/jobs", response_model=schemas.JobList)
def list_jobs(
    status: list[str] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    where = [Job.organization_id == principal.organization.id]
    if status:
        unknown = set(status) - JobStatus.ALL
        if unknown:
            raise AppError(422, "VALIDATION_ERROR", f"Unknown status: {sorted(unknown)}")
        where.append(Job.status.in_(status))
    total = session.scalar(select(func.count()).select_from(Job).where(*where)) or 0
    jobs = list(session.scalars(select(Job).where(*where).order_by(Job.created_at.desc()).limit(limit).offset(offset)))
    counts = _counts(session, [j.id for j in jobs])
    return schemas.JobList(items=[schemas.job_out(j, counts[j.id]) for j in jobs], total=total, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=schemas.JobDetail)
def get_job(job_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    return _job_out(session, _job(session, principal, job_id), detail=True)


@router.get("/jobs/{job_id}/tasks", response_model=schemas.TaskList)
def list_tasks(job_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    job = _job(session, principal, job_id)
    tasks = list(session.scalars(select(Task).where(Task.job_id == job.id).order_by(Task.priority, Task.key)))
    by_id = {t.id: t for t in tasks}
    deps: dict[uuid.UUID, list[str]] = {}
    if tasks:
        for d in session.scalars(select(TaskDependency).where(TaskDependency.task_id.in_(by_id.keys()))):
            deps.setdefault(d.task_id, []).append(by_id[d.depends_on_task_id].key)
    return schemas.TaskList(items=[schemas.task_out(t, deps.get(t.id, [])) for t in tasks])


@router.get("/jobs/{job_id}/artifacts", response_model=schemas.ArtifactList)
def list_job_artifacts(job_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    job = _job(session, principal, job_id)
    arts = list(session.scalars(select(Artifact).where(Artifact.job_id == job.id).order_by(Artifact.created_at, Artifact.filename)))
    return schemas.ArtifactList(items=_artifacts_out(session, arts, principal))


@router.get("/artifacts", response_model=schemas.ArtifactPage)
def list_artifacts(
    kind: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    where = [Artifact.organization_id == principal.organization.id]
    if kind:
        where.append(Artifact.kind == kind)
    total = session.scalar(select(func.count()).select_from(Artifact).where(*where)) or 0
    arts = list(session.scalars(select(Artifact).where(*where).order_by(Artifact.created_at.desc()).limit(limit).offset(offset)))
    return schemas.ArtifactPage(items=_artifacts_out(session, arts, principal), total=total, limit=limit, offset=offset)


@router.put("/artifacts/{artifact_id}/text", response_model=schemas.ArtifactOut, summary="Edit a text deliverable")
def edit_artifact_text(artifact_id: uuid.UUID, body: schemas.ArtifactTextUpdate,
                       principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    art = session.scalar(select(Artifact).where(Artifact.id == artifact_id, Artifact.organization_id == principal.organization.id))
    if art is None:
        raise not_found("Artifact")
    art = orchestrator.edit_artifact_text(session, art, body.text, principal.actor)
    return _artifacts_out(session, [art], principal)[0]


@router.post("/artifacts/{artifact_id}/regenerate", status_code=202, summary="Generate a new version of an image or document")
def regenerate_artifact(artifact_id: uuid.UUID, body: schemas.ImageRegenerate,
                        principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    from app.worker.regenerate import request_regeneration

    art = session.scalar(select(Artifact).where(Artifact.id == artifact_id, Artifact.organization_id == principal.organization.id))
    if art is None:
        raise not_found("Artifact")
    guidance = ((body.concept if art.kind == "image" else body.instruction) or "").strip() or None
    request_regeneration(session, art, guidance, principal.actor)
    return {"status": "queued", "artifact_id": str(artifact_id)}


@router.post("/artifacts/{artifact_id}/regenerate-caption", response_model=schemas.CaptionRegenerateResult,
             summary="Write a new version of a caption")
def regenerate_caption(artifact_id: uuid.UUID, body: schemas.CaptionRegenerate,
                       principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    from app.agents.base import AgentContext
    from app.agents.content import rewrite_caption
    from app.errors import AgentError
    from app.worker.celery_app import rerun_qa_task

    art = session.scalar(select(Artifact).where(Artifact.id == artifact_id, Artifact.organization_id == principal.organization.id))
    if art is None:
        raise not_found("Artifact")
    if art.kind != "caption":
        raise AppError(409, "CONFLICT", "Only captions can be regenerated here")
    job = session.get(Job, art.job_id)
    task = session.get(Task, art.task_id)
    assert job is not None and task is not None
    if job.status in JobStatus.ACTIVE:
        raise AppError(409, "CONFLICT", "The job is still running. Regenerate its captions after it has finished.")
    storage = get_storage()
    siblings = session.scalars(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "caption", Artifact.id != art.id))
    others = [storage.read_bytes(a.storage_key).decode("utf-8", "replace") for a in siblings]
    current = storage.read_bytes(art.storage_key).decode("utf-8", "replace")
    org = principal.organization
    project = session.get(Project, job.project_id)
    assert project is not None
    ctx = AgentContext(session=session, job=job, task=task, org=org, project=project)
    if body.action == "tone" and not (body.tone or "").strip():
        raise AppError(422, "VALIDATION_ERROR", "Choose a tone for 'Change tone'")
    try:
        # Nothing is saved until the model has written a new version that passes the checks; on any error the
        # current caption stays exactly as it is.
        text, warnings, demo, meta = rewrite_caption(ctx, current, others, (body.instruction or "").strip() or None,
                                                     action=body.action, tone=(body.tone or "").strip() or None,
                                                     current_is_demo=art.is_demo)
    except AgentError as exc:
        status = {"PROVIDER_NOT_CONFIGURED": 409, "PRIVACY_POLICY_BLOCKED": 409, "BUDGET_EXCEEDED": 402,
                  "MONTHLY_BUDGET_EXCEEDED": 402, "PROVIDER_NO_CREDIT": 402, "CAPTION_QUALITY_FAILED": 422}.get(exc.code, 502)
        raise AppError(status, exc.code, exc.message) from exc
    finally:
        ctx.cleanup()
    art = orchestrator.edit_artifact_text(session, art, text, principal.actor, source=body.action)
    art.is_demo = demo
    art.meta = {**(art.meta or {}), "regenerated": True, **{k: v for k, v in meta.items() if k != "attempts"}, "quality": warnings}
    orchestrator.audit(session, art.organization_id, "artifact.regenerated", principal.actor, job_id=job.id, task_key=task.key,
                       detail={"file": art.filename, "action": body.action, "instruction": bool(body.instruction),
                               "warnings": len(warnings), "attempts": meta.get("attempts", 1)})
    session.commit()
    rerun_qa_task.apply_async(args=[str(job.id), principal.actor], queue="qa")
    return schemas.CaptionRegenerateResult(artifact=_artifacts_out(session, [art], principal)[0], warnings=warnings)


@router.delete("/artifacts", response_model=schemas.ArtifactsDeleteResult, summary="Permanently delete all stored deliverables")
def delete_all_artifacts(
    kind: str | None = Query(default=None, max_length=24),
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_db),
):
    return orchestrator.delete_artifacts(session, principal.organization.id, principal.actor, kind or None)


@router.get("/jobs/{job_id}/events", response_model=schemas.EventList)
def list_events(job_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    job = _job(session, principal, job_id)
    rows = session.scalars(select(AuditLog).where(AuditLog.organization_id == job.organization_id, AuditLog.job_id == job.id)
                           .order_by(AuditLog.created_at, AuditLog.id).limit(1000))
    return schemas.EventList(items=[schemas.EventOut(id=r.id, at=r.created_at, action=r.action, actor=r.actor,
                                                     task_key=r.task_key, detail=r.detail or {}) for r in rows])


@router.post("/jobs/{job_id}/events", response_model=schemas.EventOut, status_code=201)
def add_event(job_id: uuid.UUID, body: schemas.EventCreate, principal: Principal = Depends(get_principal),
              session: Session = Depends(get_db)):
    job = _job(session, principal, job_id)
    if not body.action.startswith("n8n."):
        raise AppError(422, "VALIDATION_ERROR", "External events must use the 'n8n.' action prefix")
    row = AuditLog(organization_id=job.organization_id, job_id=job.id, actor=principal.actor, action=body.action,
                   detail=body.detail)
    session.add(row)
    session.commit()
    return schemas.EventOut(id=row.id, at=row.created_at, action=row.action, actor=row.actor, task_key=None, detail=row.detail)


@router.post("/jobs/{job_id}/cancel", response_model=schemas.JobOut)
def cancel_job(job_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    job = orchestrator.cancel_job(session, _job(session, principal, job_id), principal.actor)
    return _job_out(session, job)


@router.delete("/jobs", response_model=schemas.BulkDeleteResult, summary="Permanently delete all finished jobs")
def delete_all_jobs(
    status: list[str] | None = Query(default=None),
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_db),
):
    if status and (unknown := set(status) - JobStatus.ALL):
        raise AppError(422, "VALIDATION_ERROR", f"Unknown status: {sorted(unknown)}")
    return orchestrator.delete_jobs(session, principal.organization.id, principal.actor, status)


@router.delete("/jobs/{job_id}", status_code=204, summary="Permanently delete a finished job and its files")
def delete_job(job_id: uuid.UUID, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    orchestrator.delete_job(session, _job(session, principal, job_id), principal.actor)
    return Response(status_code=204)


@router.post("/jobs/{job_id}/approve", response_model=schemas.JobOut)
def approve_job(job_id: uuid.UUID, body: schemas.ApprovalDecision, principal: Principal = Depends(require_admin),
                session: Session = Depends(get_db)):
    job = orchestrator.decide_approval(session, _job(session, principal, job_id), body.decision, body.comment, principal.actor)
    return _job_out(session, job)


@router.post("/jobs/{job_id}/plan", response_model=schemas.JobOut, status_code=202)
def plan_job(job_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    job = _job(session, principal, job_id)
    orchestrator.start_planning(session, job, principal.actor)
    return _job_out(session, job)


@router.post("/jobs/{job_id}/dispatch", response_model=schemas.DispatchResponse, status_code=202)
def dispatch_job(job_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    job = _job(session, principal, job_id)
    dispatched = orchestrator.start_dispatch(session, job, principal.actor)
    return schemas.DispatchResponse(job=_job_out(session, job), dispatched=dispatched)


@router.get("/jobs/{job_id}/qa", response_model=schemas.QAResponse)
def job_qa(job_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    job = _job(session, principal, job_id)
    checks = (job.qa_report or {}).get("checks", [])
    return schemas.QAResponse(verdict=job.qa_verdict, summary=job.qa_summary,
                              checks=[schemas.QACheck(name=c["name"], status=c["status"], detail=c["detail"]) for c in checks])


@router.get("/dashboard/summary", response_model=schemas.DashboardSummary)
def dashboard(principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    org = principal.organization
    by_status = dict(session.execute(
        select(Job.status, func.count()).where(Job.organization_id == org.id).group_by(Job.status)).all())
    active_est = session.scalar(select(func.coalesce(func.sum(Job.estimated_cost_usd), 0)).where(
        Job.organization_id == org.id, Job.status.in_(JobStatus.ACTIVE))) or 0
    arts = list(session.scalars(
        select(Artifact).where(Artifact.organization_id == org.id,
                               Artifact.kind.in_(["image", "caption", "document_pdf", "document_docx", "video"]))
        .order_by(Artifact.created_at.desc()).limit(12)))
    return schemas.DashboardSummary(
        active_jobs=sum(by_status.get(s, 0) for s in JobStatus.ACTIVE),
        completed_jobs=by_status.get(JobStatus.COMPLETED, 0),
        failed_jobs=by_status.get(JobStatus.FAILED, 0) + by_status.get(JobStatus.PARTIAL, 0),
        awaiting_approval_jobs=by_status.get(JobStatus.AWAITING_APPROVAL, 0),
        month_cost_usd=float(month_spend(session, org.id)), active_estimated_cost_usd=float(active_est),
        monthly_budget_usd=float(org.monthly_budget_usd), recent_artifacts=_artifacts_out(session, arts, principal),
        offline_queues=[q for q, n in queue_status(session).items() if n == 0],
    )


@router.get("/usage", response_model=schemas.UsageOut)
def usage(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    today = datetime.now(UTC).date()
    start = from_ or today.replace(day=1)
    end = to or today
    if end < start or (end - start).days > 366:
        raise AppError(422, "VALIDATION_ERROR", "Invalid period (max 366 days, 'to' must not precede 'from')")
    lo = datetime(start.year, start.month, start.day, tzinfo=UTC)
    hi = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
    org = principal.organization
    events = list(session.scalars(select(UsageEvent).where(
        UsageEvent.organization_id == org.id, UsageEvent.created_at >= lo, UsageEvent.created_at < hi)))
    by_provider: dict[str, list] = {}
    by_agent: dict[str, list] = {}
    daily: dict[str, Decimal] = {}
    total = Decimal("0")
    for e in events:
        total += e.cost_usd
        for bucket, key in ((by_provider, e.provider), (by_agent, e.agent)):
            item = bucket.setdefault(key, [Decimal("0"), 0])
            item[0] += e.cost_usd
            item[1] += 1
        day = e.created_at.date().isoformat()
        daily[day] = daily.get(day, Decimal("0")) + e.cost_usd
    return schemas.UsageOut(
        period={"from": start.isoformat(), "to": end.isoformat()}, total_cost_usd=float(total),
        monthly_budget_usd=float(org.monthly_budget_usd),
        by_provider=[schemas.ProviderUsage(provider=k, cost_usd=float(v[0]), events=v[1]) for k, v in sorted(by_provider.items())],
        by_agent=[schemas.AgentUsage(agent=k, cost_usd=float(v[0]), events=v[1]) for k, v in sorted(by_agent.items())],
        daily=[schemas.DailyUsage(date=k, cost_usd=float(v)) for k, v in sorted(daily.items())],
    )


# --- signed downloads (no auth header: the HMAC signature is the capability) -------------------
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


@public_router.get("/artifacts/{artifact_id}/download", summary="Download an artifact via a signed, expiring URL")
def download_artifact(artifact_id: uuid.UUID, exp: int, sig: str, request: Request, session: Session = Depends(get_db)):
    if not verify_artifact_signature(artifact_id, exp, sig):
        raise AppError(403, "FORBIDDEN", "Download link is invalid or has expired")
    art = session.get(Artifact, artifact_id)
    if art is None:
        raise not_found("Artifact")
    # A signed link is a shareable capability: it only ever serves released (approved) work, even if a
    # link was issued before the job went back to review or was rejected.
    if not _released(session, art):
        raise AppError(403, "NOT_RELEASED", "This file is a draft awaiting approval and cannot be downloaded yet")
    return _stream_artifact(art, request, cache="private, max-age=300")


@router.get("/artifacts/{artifact_id}/preview", summary="Preview a file (including drafts) as a signed-in reviewer")
def preview_artifact(artifact_id: uuid.UUID, request: Request, principal: Principal = Depends(get_principal),
                     session: Session = Depends(get_db)):
    """Internal review of drafts: requires a signed-in person of the same organisation; API keys
    (automations) are refused for drafts, so nothing unapproved can be delivered automatically."""
    art = session.scalar(select(Artifact).where(Artifact.id == artifact_id,
                                                Artifact.organization_id == principal.organization.id))
    if art is None:
        raise not_found("Artifact")
    if principal.user is None and not _released(session, art):
        raise AppError(403, "NOT_RELEASED", "Drafts are only visible to signed-in reviewers until approved")
    resp = _stream_artifact(art, request, cache="private, no-store")
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _released(session: Session, art: Artifact) -> bool:
    status = session.scalar(select(Job.status).where(Job.id == art.job_id))
    return status in schemas.RELEASED_JOB_STATUSES


def _stream_artifact(art: Artifact, request: Request, *, cache: str) -> Response:
    storage = get_storage()
    if not storage.exists(art.storage_key):
        raise not_found("Artifact")
    size = storage.size(art.storage_key)
    inline = art.content_type.split("/")[0] in ("image", "video", "audio", "text") or art.content_type == "application/pdf"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{art.filename}"',
        "Cache-Control": cache,
        "X-Content-Type-Options": "nosniff",
    }
    start, end, status = 0, size - 1, 200
    if (rng := request.headers.get("range")) and size > 0:
        m = _RANGE.match(rng.strip())
        if not m or (not m[1] and not m[2]):
            raise AppError(416, "RANGE_NOT_SATISFIABLE", "Invalid Range header")
        if m[1]:
            start = int(m[1])
            end = min(int(m[2]), size - 1) if m[2] else size - 1
        else:
            start = max(size - int(m[2]), 0)
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(max(end - start + 1, 0))
    body = storage.iter_range(art.storage_key, start, end) if size else iter(())
    return StreamingResponse(body, status_code=status, media_type=art.content_type, headers=headers)
