"""Job/task state machine. PostgreSQL is the source of truth; Celery only carries "run task X" messages.

Guarantees:
- A task runs only after an atomic claim (UPDATE ... WHERE status='queued'), so duplicate or
  redelivered messages are harmless.
- State is committed BEFORE a message is sent; a lost message is recovered by the reaper
  (lease expiry), so an accepted job is never silently dropped.
- A job is `completed` only if every deliverable task succeeded and QA verified its files.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.errors import conflict
from app.models import (
    Approval,
    Artifact,
    AuditLog,
    Job,
    JobStatus,
    Organization,
    Project,
    Task,
    TaskDependency,
    TaskStatus,
)
from app.services.storage import get_storage

log = logging.getLogger("mcr.orchestrator")

DELIVERABLE_AGENTS = ("content", "image", "document", "video")


def audit(session: Session, org_id: uuid.UUID, action: str, actor: str, *, job_id: uuid.UUID | None = None,
          task_key: str | None = None, detail: dict[str, Any] | None = None) -> None:
    session.add(AuditLog(organization_id=org_id, job_id=job_id, task_key=task_key, actor=actor, action=action,
                         detail=detail or {}))


def _rows(result: Any) -> int:
    """Rows affected by an UPDATE (CursorResult.rowcount)."""
    return int(result.rowcount)


def send_task(task_id: uuid.UUID, queue: str, countdown: float = 0) -> None:
    from app.worker.celery_app import run_task

    run_task.apply_async(args=[str(task_id)], queue=queue, countdown=countdown or None)


# --- job creation -----------------------------------------------------------------
def default_project(session: Session, org: Organization) -> Project:
    project = session.scalar(select(Project).where(Project.organization_id == org.id).order_by(Project.created_at))
    if project is None:
        project = Project(organization_id=org.id, name="Default", brand_guidelines={})
        session.add(project)
        session.flush()
    return project


def create_job(session: Session, org: Organization, *, command: str, agents: list[str], budget_limit_usd: Decimal,
               language: str, aspect_ratio: str, require_approval: bool, data_classification: str,
               project: Project, auto_start: bool, idempotency_key: str | None, user_id: uuid.UUID | None,
               actor: str, options: dict[str, Any] | None = None) -> tuple[Job, bool]:
    """Returns (job, created). Idempotent on (organization, idempotency_key)."""
    def existing() -> Job | None:
        if not idempotency_key:
            return None
        return session.scalar(select(Job).where(Job.organization_id == org.id, Job.idempotency_key == idempotency_key))

    if (job := existing()) is not None:
        return job, False
    settings = get_settings()
    job = Job(organization_id=org.id, project_id=project.id, created_by_user_id=user_id, command=command,
              requested_agents=agents, budget_limit_usd=budget_limit_usd, language=language, aspect_ratio=aspect_ratio,
              require_approval=require_approval, data_classification=data_classification, auto_start=auto_start,
              idempotency_key=idempotency_key, status=JobStatus.QUEUED, options=options or None)
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        if (job2 := existing()) is not None:
            return job2, False
        raise
    now = utcnow()
    planner = Task(organization_id=org.id, job_id=job.id, key="plan", agent="planner", title="Plan the job", priority=1,
                   max_attempts=settings.task_max_attempts,
                   status=TaskStatus.QUEUED if auto_start else TaskStatus.PENDING,
                   queued_at=now if auto_start else None,
                   lease_expires_at=now + timedelta(seconds=settings.task_queue_redelivery_seconds) if auto_start else None)
    session.add(planner)
    audit(session, org.id, "job.created", actor, job_id=job.id,
          detail={"agents": agents, "budget_limit_usd": str(budget_limit_usd), "auto_start": auto_start})
    session.commit()
    if auto_start:
        send_task(planner.id, "planner")
    return job, True


def start_planning(session: Session, job: Job, actor: str) -> None:
    """Enqueue the planner for a job created with auto_start=false. Idempotent."""
    settings = get_settings()
    now = utcnow()
    planner = session.scalar(select(Task).where(Task.job_id == job.id, Task.agent == "planner"))
    if planner is None:
        return
    claimed = _rows(session.execute(
        update(Task).where(Task.id == planner.id, Task.status == TaskStatus.PENDING).values(
            status=TaskStatus.QUEUED, queued_at=now,
            lease_expires_at=now + timedelta(seconds=settings.task_queue_redelivery_seconds))
    ))
    if claimed:
        audit(session, job.organization_id, "job.plan_requested", actor, job_id=job.id)
    session.commit()
    if claimed:
        send_task(planner.id, "planner")


def apply_plan(session: Session, job: Job, plan: dict[str, Any]) -> None:
    from app.agents.planner import Plan

    parsed = Plan.model_validate(plan)
    already = session.scalar(select(func.count()).select_from(Task).where(Task.job_id == job.id, Task.agent != "planner"))
    if already:
        return
    settings = get_settings()
    by_key: dict[str, Task] = {}
    for pt in parsed.tasks:
        task = Task(organization_id=job.organization_id, job_id=job.id, key=pt.key, agent=pt.agent, title=pt.title,
                    params=pt.params, priority=pt.priority, estimated_cost_usd=pt.estimated_cost_usd,
                    max_attempts=settings.task_max_attempts, status=TaskStatus.PENDING)
        session.add(task)
        by_key[pt.key] = task
    session.flush()
    for pt in parsed.tasks:
        for dep_key, soft in [(k, False) for k in pt.depends_on] + [(k, True) for k in pt.waits_for]:
            session.add(TaskDependency(organization_id=job.organization_id, task_id=by_key[pt.key].id,
                                       depends_on_task_id=by_key[dep_key].id, soft=soft))
    job.plan = plan
    job.planner_mode = parsed.planner_mode
    job.estimated_cost_usd = parsed.estimated_cost_usd
    job.status = JobStatus.RUNNING if job.auto_start else JobStatus.PLANNED
    job.started_at = job.started_at or utcnow()
    audit(session, job.organization_id, "job.planned", "worker", job_id=job.id,
          detail={"planner_mode": parsed.planner_mode, "tasks": [t.key for t in parsed.tasks],
                  "estimated_cost_usd": str(parsed.estimated_cost_usd), "notes": parsed.notes})
    session.commit()


def start_dispatch(session: Session, job: Job, actor: str) -> list[str]:
    """Move a `planned` job to `running` and enqueue its ready tasks (n8n-driven flow). Idempotent."""
    if job.status == JobStatus.PLANNED:
        job.status = JobStatus.RUNNING
        audit(session, job.organization_id, "job.dispatch_requested", actor, job_id=job.id)
        session.commit()
    elif job.status != JobStatus.RUNNING:
        raise conflict(f"Job is '{job.status}'; it must be 'planned' before dispatch")
    return dispatch_ready(session, job.id)


# --- dispatch ---------------------------------------------------------------------------
def dispatch_ready(session: Session, job_id: uuid.UUID) -> list[str]:
    """Queue every task whose dependencies are satisfied. Returns the keys that were queued."""
    settings = get_settings()
    session.expire_all()
    job = session.get(Job, job_id, with_for_update=True)  # serializes dispatchers per job on PostgreSQL
    if job is None or job.status != JobStatus.RUNNING or job.cancel_requested:
        session.commit()
        finalize_job(session, job_id)
        return []
    tasks = {t.id: t for t in session.scalars(select(Task).where(Task.job_id == job_id))}
    deps: dict[uuid.UUID, list[tuple[Task, bool]]] = {}
    for d in session.scalars(select(TaskDependency).where(TaskDependency.task_id.in_(tasks.keys()))):
        deps.setdefault(d.task_id, []).append((tasks[d.depends_on_task_id], d.soft))

    now = utcnow()
    to_send: list[Task] = []
    changed = True
    while changed:
        changed = False
        for task in sorted(tasks.values(), key=lambda t: (t.priority, t.key)):
            if task.status != TaskStatus.PENDING:
                continue
            hard = [d for d, soft in deps.get(task.id, []) if not soft]
            soft_deps = [d for d, soft in deps.get(task.id, []) if soft]
            broken = [d.key for d in hard if d.status in (TaskStatus.FAILED, TaskStatus.SKIPPED, TaskStatus.CANCELLED)]
            if broken:
                task.status = TaskStatus.SKIPPED
                task.error_code = "DEPENDENCY_FAILED"
                task.error_message = f"Required task(s) did not succeed: {', '.join(broken)}"
                task.finished_at = now
                audit(session, job.organization_id, "task.skipped", "system", job_id=job.id, task_key=task.key,
                      detail={"failed_dependencies": broken})
                changed = True
            elif all(d.status == TaskStatus.SUCCEEDED for d in hard) and all(d.status in TaskStatus.TERMINAL for d in soft_deps):
                task.status = TaskStatus.QUEUED
                task.queued_at = now
                task.lease_expires_at = now + timedelta(seconds=settings.task_queue_redelivery_seconds)
                audit(session, job.organization_id, "task.queued", "system", job_id=job.id, task_key=task.key,
                      detail={"queue": task.agent})
                to_send.append(task)
    session.commit()  # state first, messages second: a crash here is healed by the reaper
    for task in to_send:
        send_task(task.id, task.agent)
    finalize_job(session, job_id)
    return [t.key for t in to_send]


# --- task lifecycle (called by workers) -------------------------------------------------------
def claim_task(session: Session, task_id: uuid.UUID) -> Task | None:
    settings = get_settings()
    now = utcnow()
    claimed = _rows(session.execute(
        update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED).values(
            status=TaskStatus.RUNNING, attempt=Task.attempt + 1, started_at=now, heartbeat_at=now, finished_at=None,
            lease_expires_at=now + timedelta(seconds=settings.task_lease_seconds))
    ))
    session.commit()
    if not claimed:
        return None
    session.expire_all()
    return session.get(Task, task_id)


def purge_task_artifacts(session: Session, task: Task) -> None:
    """Remove files of earlier attempts so a retry never leaves stale deliverables behind."""
    storage = get_storage()
    for art in session.scalars(select(Artifact).where(Artifact.task_id == task.id)):
        try:
            storage.delete(art.storage_key)
        except Exception:  # noqa: BLE001
            log.warning("could not delete stale artifact %s", art.storage_key)
        session.delete(art)
    session.commit()


def _still_owner(session: Session, task: Task, values: dict[str, Any]) -> bool:
    """Write the task outcome only if this worker still holds the claim (same attempt, still running)."""
    owned = session.execute(
        update(Task).where(Task.id == task.id, Task.status == TaskStatus.RUNNING, Task.attempt == task.attempt)
        .values(**values)
    )
    return bool(_rows(owned))


def complete_task(session: Session, task: Task, result: dict[str, Any], used_demo: bool) -> None:
    job_id = task.job_id
    if not _still_owner(session, task, {"status": TaskStatus.SUCCEEDED, "result": result, "finished_at": utcnow(),
                                        "lease_expires_at": None, "error_code": None, "error_message": None}):
        session.rollback()
        log.warning("task %s finished but its claim was lost; result discarded", task.id)
        return
    audit(session, task.organization_id, "task.succeeded", "worker", job_id=job_id, task_key=task.key,
          detail={"attempt": task.attempt, "provider": task.provider, "location": task.execution_location})
    if used_demo:
        session.execute(update(Job).where(Job.id == job_id).values(demo_mode=True))
    session.commit()
    session.expire_all()
    job = session.get(Job, job_id)
    assert job is not None
    if task.agent == "planner":
        apply_plan(session, job, result["plan"])
    elif task.agent == "qa":
        _apply_qa(session, job, result)
    dispatch_ready(session, job_id)


def fail_task(session: Session, task: Task, code: str, message: str, *, retryable: bool) -> None:
    settings = get_settings()
    job_id = task.job_id
    now = utcnow()
    message = message[:2000]
    if retryable and task.attempt < task.max_attempts:
        delay = min(settings.retry_base_seconds * (2 ** (task.attempt - 1)), settings.retry_max_seconds)
        if not _still_owner(session, task, {
            "status": TaskStatus.QUEUED, "error_code": code, "error_message": message, "queued_at": now,
            "lease_expires_at": now + timedelta(seconds=delay + settings.task_queue_redelivery_seconds)}):
            session.rollback()
            return
        audit(session, task.organization_id, "task.retry_scheduled", "worker", job_id=job_id, task_key=task.key,
              detail={"attempt": task.attempt, "max_attempts": task.max_attempts, "delay_seconds": delay,
                      "error_code": code, "error": message[:300]})
        session.commit()
        send_task(task.id, task.agent, countdown=delay)
        return
    if not _still_owner(session, task, {"status": TaskStatus.FAILED, "error_code": code, "error_message": message,
                                        "finished_at": now, "lease_expires_at": None}):
        session.rollback()
        return
    # Dead letter: retries exhausted or error is permanent. Recorded for operators in the audit trail.
    audit(session, task.organization_id, "task.failed", "worker", job_id=job_id, task_key=task.key,
          detail={"attempt": task.attempt, "error_code": code, "error": message[:300], "dead_letter": True})
    if task.agent == "planner":
        session.execute(update(Job).where(Job.id == job_id).values(
            status=JobStatus.FAILED, error_code=code, error_message=message, finished_at=now))
        audit(session, task.organization_id, "job.failed", "system", job_id=job_id, detail={"error_code": code})
    session.commit()
    dispatch_ready(session, job_id)


def cancel_task(session: Session, task: Task) -> None:
    if _still_owner(session, task, {"status": TaskStatus.CANCELLED, "finished_at": utcnow(), "lease_expires_at": None}):
        audit(session, task.organization_id, "task.cancelled", "worker", job_id=task.job_id, task_key=task.key)
    session.commit()
    finalize_job(session, task.job_id)


def _apply_qa(session: Session, job: Job, report: dict[str, Any]) -> None:
    job.qa_verdict = report["verdict"]
    job.qa_summary = report["summary"]
    job.qa_report = report
    by_key = {t.key: t for t in session.scalars(select(Task).where(Task.job_id == job.id))}
    audit(session, job.organization_id, "qa.verdict", "worker", job_id=job.id, task_key="qa_review",
          detail={"verdict": report["verdict"], "summary": report["summary"]})
    if report["verdict"] == "REVISE":
        job.qa_revisions += 1
        for key in [*report.get("revise_tasks", []), "qa_review"]:
            task = by_key.get(key)
            if task is None:
                continue
            task.status = TaskStatus.PENDING
            task.max_attempts = max(task.max_attempts, task.attempt + 1)
            task.result = None
            task.finished_at = None
            audit(session, job.organization_id, "task.revision_requested", "worker", job_id=job.id, task_key=key,
                  detail={"revision": job.qa_revisions})
    else:
        for key in report.get("reject_tasks", []):
            task = by_key.get(key)
            if task is not None and task.status == TaskStatus.SUCCEEDED:
                task.status = TaskStatus.FAILED
                task.error_code = "QA_REJECTED"
                task.error_message = "Deliverable failed QA verification after the allowed revisions"
                audit(session, job.organization_id, "task.failed", "worker", job_id=job.id, task_key=key,
                      detail={"error_code": "QA_REJECTED", "dead_letter": True})
    session.commit()


# --- job completion ------------------------------------------------------------------------------
def finalize_job(session: Session, job_id: uuid.UUID) -> None:
    session.expire_all()
    job = session.get(Job, job_id, with_for_update=True)
    if job is None or job.status in JobStatus.TERMINAL or job.status == JobStatus.AWAITING_APPROVAL:
        session.commit()
        return
    tasks = list(session.scalars(select(Task).where(Task.job_id == job_id)))
    now = utcnow()
    if job.cancel_requested:
        if not any(t.status == TaskStatus.RUNNING for t in tasks):
            job.status = JobStatus.CANCELLED
            job.finished_at = now
            audit(session, job.organization_id, "job.cancelled", "system", job_id=job.id)
        session.commit()
        return
    if job.status != JobStatus.RUNNING or any(t.status not in TaskStatus.TERMINAL for t in tasks):
        session.commit()
        return

    deliverables = [t for t in tasks if t.agent in DELIVERABLE_AGENTS]
    ok = [t for t in deliverables if t.status == TaskStatus.SUCCEEDED]
    bad = [t for t in deliverables if t.status != TaskStatus.SUCCEEDED]
    qa = next((t for t in tasks if t.agent == "qa"), None)
    if not ok:
        job.status = JobStatus.FAILED
        job.error_code = next((t.error_code for t in bad if t.error_code), "ALL_TASKS_FAILED")
        job.error_message = "No deliverable could be generated. See the individual tasks for details."
    elif bad:
        job.status = JobStatus.PARTIAL
        job.error_code = "PARTIAL_FAILURE"
        job.error_message = "Not generated: " + ", ".join(f"{t.key} ({t.error_code})" for t in bad)
    else:
        qa_ok = qa is not None and qa.status == TaskStatus.SUCCEEDED and job.qa_verdict is not None
        if not qa_ok:
            job.qa_verdict = "HUMAN_REVIEW"
            job.qa_summary = "Automated QA could not run; a human must verify the deliverables."
        if job.qa_verdict == "PASS" and not job.require_approval:
            job.status = JobStatus.COMPLETED
        else:
            job.status = JobStatus.AWAITING_APPROVAL
            job.approval_status = "pending"
            reason = "Approval required by job settings" if job.qa_verdict == "PASS" else (job.qa_summary or "QA escalation")
            session.add(Approval(organization_id=job.organization_id, job_id=job.id, status="pending", reason=reason))
    if job.status in JobStatus.TERMINAL:
        job.finished_at = now
    audit(session, job.organization_id, f"job.{job.status}", "system", job_id=job.id,
          detail={"qa_verdict": job.qa_verdict, "succeeded": [t.key for t in ok], "not_generated": [t.key for t in bad]})
    session.commit()


def cancel_job(session: Session, job: Job, actor: str) -> Job:
    if job.status in JobStatus.TERMINAL:
        raise conflict(f"Job is already {job.status}")
    now = utcnow()
    job.cancel_requested = True
    was_waiting = job.status == JobStatus.AWAITING_APPROVAL
    job.status = JobStatus.CANCELLING
    session.execute(
        update(Task).where(Task.job_id == job.id, Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED]))
        .values(status=TaskStatus.CANCELLED, finished_at=now, lease_expires_at=None)
    )
    if was_waiting:
        session.execute(update(Approval).where(Approval.job_id == job.id, Approval.status == "pending")
                        .values(status="rejected", comment="Job cancelled", decided_by=actor, decided_at=now))
        job.approval_status = None
    audit(session, job.organization_id, "job.cancel_requested", actor, job_id=job.id)
    session.commit()
    finalize_job(session, job.id)
    session.refresh(job)
    return job


def decide_approval(session: Session, job: Job, decision: str, comment: str | None, actor: str) -> Job:
    if job.status != JobStatus.AWAITING_APPROVAL:
        raise conflict(f"Job is '{job.status}'; only jobs awaiting approval can be approved or rejected")
    now = utcnow()
    approval = session.scalar(select(Approval).where(Approval.job_id == job.id, Approval.status == "pending")
                              .order_by(Approval.created_at.desc()))
    if approval is None:
        approval = Approval(organization_id=job.organization_id, job_id=job.id)
        session.add(approval)
    approval.status = decision
    approval.comment = comment
    approval.decided_by = actor
    approval.decided_at = now
    job.approval_status = decision
    job.status = JobStatus.COMPLETED if decision == "approved" else JobStatus.REJECTED
    job.finished_at = now
    audit(session, job.organization_id, f"job.{decision}", actor, job_id=job.id, detail={"comment": comment or ""})
    session.commit()
    return job


def delete_job(session: Session, job: Job, actor: str) -> int:
    """Permanently delete a finished job with its tasks, files and timeline. Returns the number of files removed.

    Usage events are kept (job_id becomes NULL) so spending history and budgets stay correct.
    """
    if job.status in JobStatus.ACTIVE:
        raise conflict(f"Job is '{job.status}'. Cancel it and wait until it has stopped before deleting.")
    storage = get_storage()
    keys = list(session.scalars(select(Artifact.storage_key).where(Artifact.job_id == job.id)))
    for key in keys:
        try:
            storage.delete(key)
        except Exception:  # noqa: BLE001 - a missing file must not block removing the record
            log.warning("could not delete artifact file %s", key)
    org_id, job_id, command = job.organization_id, job.id, job.command
    # Database cascades remove tasks, dependencies, artifacts, approvals and the job's audit trail.
    session.execute(sa_delete(Job).where(Job.id == job_id))
    audit(session, org_id, "job.deleted", actor,
          detail={"job_id": str(job_id), "command": command[:200], "files_removed": len(keys)})
    session.commit()
    return len(keys)


def delete_jobs(session: Session, org_id: uuid.UUID, actor: str, statuses: list[str] | None = None) -> dict[str, int]:
    """Permanently delete every finished job of an organization (optionally only the given statuses).

    Jobs that are still active are left alone and counted in `skipped_active`.
    """
    where = [Job.organization_id == org_id]
    if statuses:
        where.append(Job.status.in_(statuses))
    active = session.scalar(select(func.count()).select_from(Job).where(*where, Job.status.in_(JobStatus.ACTIVE))) or 0
    job_ids = list(session.scalars(select(Job.id).where(*where, Job.status.notin_(JobStatus.ACTIVE))))
    files = 0
    if job_ids:
        storage = get_storage()
        for key in session.scalars(select(Artifact.storage_key).where(Artifact.job_id.in_(job_ids))):
            try:
                storage.delete(key)
                files += 1
            except Exception:  # noqa: BLE001 - a missing file must not block removing the records
                log.warning("could not delete artifact file %s", key)
        session.execute(sa_delete(Job).where(Job.id.in_(job_ids)))
        audit(session, org_id, "jobs.deleted_all", actor,
              detail={"deleted": len(job_ids), "files_removed": files, "skipped_active": active, "statuses": statuses or "all"})
    session.commit()
    return {"deleted": len(job_ids), "skipped_active": int(active), "files_removed": files}


def delete_artifacts(session: Session, org_id: uuid.UUID, actor: str, kind: str | None = None) -> dict[str, int]:
    """Permanently delete the organization's stored deliverables (optionally one kind). Jobs are kept.

    Files of jobs that are still in progress are left alone (a running agent may still need them)
    and counted in `skipped_active`.
    """
    where = [Artifact.organization_id == org_id]
    if kind:
        where.append(Artifact.kind == kind)
    active_jobs = select(Job.id).where(Job.organization_id == org_id, Job.status.in_(JobStatus.ACTIVE))
    skipped = session.scalar(select(func.count()).select_from(Artifact).where(*where, Artifact.job_id.in_(active_jobs))) or 0
    rows = session.execute(select(Artifact.id, Artifact.storage_key).where(*where, Artifact.job_id.notin_(active_jobs))).all()
    if rows:
        storage = get_storage()
        for _id, key in rows:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 - a missing file must not block removing the record
                log.warning("could not delete artifact file %s", key)
        session.execute(sa_delete(Artifact).where(Artifact.id.in_([r[0] for r in rows])))
        audit(session, org_id, "artifacts.deleted_all", actor,
              detail={"deleted": len(rows), "skipped_active": int(skipped), "kind": kind or "all"})
    session.commit()
    return {"deleted": len(rows), "skipped_active": int(skipped)}


# Plain-text deliverables a person may correct by hand (captions, copy, storyboards, subtitles).
EDITABLE_TEXT_TYPES = ("text/plain", "text/markdown", "application/x-subrip")


def refresh_caption_claims(session: Session, art: Artifact) -> None:
    """Recompute the unsupported-claim warnings from the caption files as they are now stored,
    so QA judges the current text rather than what the model first wrote."""
    from app.agents.content import find_unsupported_claims

    task = session.get(Task, art.task_id)
    org = session.get(Organization, art.organization_id)
    if task is None or org is None:
        return
    storage = get_storage()
    caps = sorted(session.scalars(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "caption")),
                  key=lambda a: a.filename)
    texts = [storage.read_bytes(c.storage_key).decode("utf-8", "replace") for c in caps]
    flags = [] if any(c.is_demo for c in caps) else find_unsupported_claims(texts, org.business_context or "")
    task.result = {**(task.result or {}), "claim_flags": flags}


def edit_artifact_text(session: Session, art: Artifact, text: str, actor: str) -> Artifact:
    """Replace the content of a text deliverable. The new file gets a fresh checksum so QA
    integrity checks keep passing; the edit is recorded in the job's timeline."""
    if art.content_type.split(";")[0].strip() not in EDITABLE_TEXT_TYPES:
        raise conflict("Only plain-text deliverables (captions, copy, storyboards, subtitles) can be edited")
    job = session.get(Job, art.job_id)
    if job is not None and job.status in JobStatus.ACTIVE:
        raise conflict("The job is still running. Edit its files after it has finished.")
    text = text.replace("\r\n", "\n")
    data = text.encode("utf-8")
    get_storage().put_bytes(art.storage_key, data, art.content_type)
    old_size = art.size_bytes
    art.size_bytes = len(data)
    art.sha256 = hashlib.sha256(data).hexdigest()
    art.text_preview = text[:4000]
    art.meta = {**(art.meta or {}), "edited": True, "edited_at": utcnow().isoformat(), "edited_by": actor}
    if art.kind == "caption":
        refresh_caption_claims(session, art)
    task_key = session.scalar(select(Task.key).where(Task.id == art.task_id))
    # Only sizes are logged: the text itself may be confidential.
    audit(session, art.organization_id, "artifact.edited", actor, job_id=art.job_id, task_key=task_key,
          detail={"file": art.filename, "old_size": old_size, "new_size": art.size_bytes})
    session.commit()
    return art


# --- failure recovery ---------------------------------------------------------------------------------
def reap_expired(session: Session) -> dict[str, int]:
    """Recover tasks whose worker died (running, lease expired) or whose message was lost (queued, lease expired)."""
    settings = get_settings()
    now = utcnow()
    stats = {"requeued": 0, "failed": 0, "redelivered": 0}
    expired = list(session.scalars(
        select(Task).where(Task.status.in_([TaskStatus.RUNNING, TaskStatus.QUEUED]), Task.lease_expires_at < now)
        .with_for_update(skip_locked=True).limit(200)
    ))
    resend: list[Task] = []
    touched_jobs: set[uuid.UUID] = set()
    for task in expired:
        new_lease = now + timedelta(seconds=settings.task_queue_redelivery_seconds)
        if task.status == TaskStatus.QUEUED:
            task.lease_expires_at = new_lease
            audit(session, task.organization_id, "task.redelivered", "system", job_id=task.job_id, task_key=task.key)
            resend.append(task)
            stats["redelivered"] += 1
        elif task.attempt < task.max_attempts:
            task.status = TaskStatus.QUEUED
            task.queued_at = now
            task.lease_expires_at = new_lease
            task.error_code = "LEASE_EXPIRED"
            task.error_message = "Worker stopped responding; task was re-queued"
            audit(session, task.organization_id, "task.lease_expired", "system", job_id=task.job_id, task_key=task.key,
                  detail={"attempt": task.attempt, "action": "requeued"})
            resend.append(task)
            stats["requeued"] += 1
        else:
            task.status = TaskStatus.FAILED
            task.finished_at = now
            task.lease_expires_at = None
            task.error_code = "LEASE_EXPIRED"
            task.error_message = "Worker stopped responding and retries are exhausted"
            audit(session, task.organization_id, "task.failed", "system", job_id=task.job_id, task_key=task.key,
                  detail={"error_code": "LEASE_EXPIRED", "dead_letter": True})
            if task.agent == "planner":
                session.execute(update(Job).where(Job.id == task.job_id).values(
                    status=JobStatus.FAILED, error_code="LEASE_EXPIRED", finished_at=now,
                    error_message="Planner worker stopped responding"))
            touched_jobs.add(task.job_id)
            stats["failed"] += 1
    session.commit()
    for task in resend:
        send_task(task.id, task.agent)
    for job_id in touched_jobs:
        dispatch_ready(session, job_id)
    for job_id in session.scalars(select(Job.id).where(Job.status == JobStatus.CANCELLING)):
        finalize_job(session, job_id)
    return stats
