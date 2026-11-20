"""Executes one task: atomic claim → agent → persist outcome → dispatch whatever became ready."""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import update

from app.agents import registry
from app.agents.base import AgentContext
from app.db import new_session
from app.errors import AgentError, JobCancelled
from app.models import Job, JobStatus, Organization, Project
from app.services import orchestrator

log = logging.getLogger("mcr.worker")

# Built from the agent registry (same six runners as before; Quote-to-Cash agents are not job agents).
AGENTS: dict[str, Callable[[AgentContext], dict[str, Any]]] = registry.creative_runners()


def execute_task(task_id: uuid.UUID) -> None:
    session = new_session()
    try:
        task = orchestrator.claim_task(session, task_id)
        if task is None:
            log.info("task not claimable (duplicate or cancelled message)", extra={"task_id": str(task_id)})
            return
        job = session.get(Job, task.job_id)
        org = session.get(Organization, task.organization_id)
        assert job is not None and org is not None
        project = session.get(Project, job.project_id)
        assert project is not None
        extra = {"job_id": str(job.id), "task_key": task.key, "agent": task.agent, "attempt": task.attempt}

        if job.cancel_requested or job.status in JobStatus.TERMINAL:
            orchestrator.cancel_task(session, task)
            return
        if task.agent == "planner":
            session.execute(update(Job).where(Job.id == job.id, Job.status == JobStatus.QUEUED)
                            .values(status=JobStatus.PLANNING))
        orchestrator.audit(session, org.id, "task.started", "worker", job_id=job.id, task_key=task.key,
                           detail={"attempt": task.attempt})
        session.commit()
        if task.attempt > 1:
            orchestrator.purge_task_artifacts(session, task)

        ctx = AgentContext(session=session, job=job, task=task, org=org, project=project)
        log.info("task started", extra=extra)
        try:
            result = AGENTS[task.agent](ctx)
        except JobCancelled:
            session.rollback()
            log.info("task cancelled", extra=extra)
            orchestrator.purge_task_artifacts(session, task)
            orchestrator.cancel_task(session, task)
        except AgentError as exc:
            session.rollback()
            log.warning("task failed: %s", exc.message, extra={**extra, "error_code": exc.code, "retryable": exc.retryable})
            orchestrator.purge_task_artifacts(session, task)  # never leave half-finished deliverables behind
            orchestrator.fail_task(session, task, exc.code, exc.message, retryable=exc.retryable)
        except Exception as exc:  # noqa: BLE001 - unknown errors are retried, then dead-lettered
            session.rollback()
            log.exception("task crashed", extra=extra)
            orchestrator.purge_task_artifacts(session, task)
            orchestrator.fail_task(session, task, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", retryable=True)
        else:
            log.info("task succeeded", extra=extra)
            orchestrator.complete_task(session, task, result, ctx.used_demo)
        finally:
            ctx.cleanup()
    finally:
        session.close()
