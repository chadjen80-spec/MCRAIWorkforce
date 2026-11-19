"""Execution context handed to every agent."""
from __future__ import annotations

import hashlib
import re
import tempfile
import uuid
from contextvars import Token
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import utcnow
from app.errors import JobCancelled
from app.models import Artifact, Job, Organization, Project, Task, TaskDependency, TaskStatus, UsageEvent
from app.services.router import Route
from app.services.storage import Storage, get_storage

THAI = re.compile(r"[฀-๿]")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def has_thai(text: str) -> bool:
    return bool(THAI.search(text))


@dataclass
class DependencyOutput:
    key: str
    agent: str
    status: str
    result: dict[str, Any]
    artifacts: list[Artifact]


@dataclass
class AgentContext:
    session: Session
    job: Job
    task: Task
    org: Organization
    project: Project
    settings: Settings = field(default_factory=get_settings)
    storage: Storage = field(default_factory=get_storage)
    used_demo: bool = False
    _workdir: tempfile.TemporaryDirectory | None = None

    def __post_init__(self) -> None:
        from app.services.budget import CURRENT_TASK

        # Budget reservations made while this task runs are attached to it (released in cleanup()).
        self._task_token: Token[uuid.UUID | None] | None = CURRENT_TASK.set(self.task.id)

    # -- environment ---------------------------------------------------------
    @property
    def workdir(self) -> Path:
        if self._workdir is None:
            self._workdir = tempfile.TemporaryDirectory(prefix="mcr-task-")
        return Path(self._workdir.name)

    def cleanup(self) -> None:
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None
        from app.services.budget import CURRENT_TASK, release_task

        try:
            self.session.rollback()  # nothing half-written may ride along with the release
            release_task(self.session, self.task.id)
        finally:
            token = getattr(self, "_task_token", None)
            if token is not None:
                CURRENT_TASK.reset(token)
                self._task_token = None

    @property
    def brand(self) -> dict[str, Any]:
        return {**self.settings.brand, **(self.project.brand_guidelines or {})}

    @property
    def business_context(self) -> str:
        return (self.org.business_context or "").strip()

    # -- liveness / cancellation ------------------------------------------------
    def heartbeat(self) -> None:
        """Extend the task lease and stop promptly if the job was cancelled."""
        now = utcnow()
        self.session.execute(
            update(Task)
            .where(Task.id == self.task.id)
            .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=self.settings.task_lease_seconds))
        )
        self.session.commit()
        self.check_cancelled()

    def check_cancelled(self) -> None:
        cancelled = self.session.scalar(select(Job.cancel_requested).where(Job.id == self.job.id))
        if cancelled:
            raise JobCancelled()

    # -- routing bookkeeping ------------------------------------------------------
    def use_route(self, route: Route) -> Route:
        self.task.provider = route.provider
        self.task.execution_location = route.location
        self.task.routing_reason = route.reason
        if route.demo:
            self.used_demo = True
        self.session.commit()
        return route

    # -- dependencies ----------------------------------------------------------------
    def dependencies(self) -> dict[str, DependencyOutput]:
        rows = self.session.scalars(
            select(Task).join(TaskDependency, TaskDependency.depends_on_task_id == Task.id).where(
                TaskDependency.task_id == self.task.id
            )
        ).all()
        out: dict[str, DependencyOutput] = {}
        for dep in rows:
            artifacts = list(
                self.session.scalars(select(Artifact).where(Artifact.task_id == dep.id).order_by(Artifact.created_at))
            )
            out[dep.key] = DependencyOutput(dep.key, dep.agent, dep.status, dep.result or {}, artifacts)
        return out

    def content_brief(self) -> dict[str, Any] | None:
        """Marketing copy produced by the content agent, when it ran successfully."""
        for dep in self.dependencies().values():
            if dep.agent == "content" and dep.status == TaskStatus.SUCCEEDED:
                return dep.result
        return None

    # -- outputs ----------------------------------------------------------------------
    def save_artifact(
        self,
        kind: str,
        filename: str,
        content_type: str,
        *,
        data: bytes | None = None,
        path: Path | None = None,
        is_demo: bool = False,
        metadata: dict[str, Any] | None = None,
        text_preview: str | None = None,
    ) -> Artifact:
        assert (data is None) != (path is None), "provide exactly one of data/path"
        filename = _SAFE.sub("-", filename).strip("-") or "artifact"
        # A short random part keeps every saved version distinct (e.g. a regenerated image never
        # overwrites the file it is about to replace), independent of the attempt counter.
        key = f"{self.org.id}/{self.job.id}/{self.task.key}/a{self.task.attempt}-{uuid.uuid4().hex[:8]}/{filename}"
        if data is not None:
            sha, size = hashlib.sha256(data).hexdigest(), len(data)
            self.storage.put_bytes(key, data, content_type)
        else:
            assert path is not None
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            sha, size = digest.hexdigest(), path.stat().st_size
            self.storage.put_file(key, path, content_type)
        artifact = Artifact(
            organization_id=self.org.id, job_id=self.job.id, task_id=self.task.id, kind=kind, filename=filename,
            content_type=content_type, size_bytes=size, sha256=sha, storage_key=key, is_demo=is_demo,
            text_preview=text_preview[:4000] if text_preview else None, meta=metadata or {},
        )
        if is_demo:
            self.used_demo = True
        self.session.add(artifact)
        self.session.commit()
        return artifact

    def record_usage(self, provider: str, operation: str, units: dict[str, Any], cost_usd: Decimal) -> None:
        self.session.add(
            UsageEvent(
                organization_id=self.org.id, job_id=self.job.id, task_id=self.task.id, agent=self.task.agent,
                provider=provider, operation=operation, units=units, cost_usd=cost_usd,
            )
        )
        # Atomic: book the cost and release the matching part of this task's budget reservation.
        from app.services.budget import settle

        settle(self.session, org_id=self.org.id, job_id=self.job.id, task_id=self.task.id, cost=cost_usd)
        self.session.commit()
