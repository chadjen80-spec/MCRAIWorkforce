"""SQLAlchemy models. Every tenant-owned table carries organization_id."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UtcDateTime, utcnow

Money = Numeric(14, 6)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class JobStatus:
    QUEUED = "queued"
    PLANNING = "planning"
    PLANNED = "planned"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

    TERMINAL = frozenset({COMPLETED, PARTIAL, FAILED, CANCELLED, REJECTED})
    ACTIVE = frozenset({QUEUED, PLANNING, PLANNED, RUNNING, CANCELLING})
    ALL = TERMINAL | ACTIVE | {AWAITING_APPROVAL}


class TaskStatus:
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    TERMINAL = frozenset({SUCCEEDED, FAILED, SKIPPED, CANCELLED})
    ALL = TERMINAL | {PENDING, QUEUED, RUNNING}


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    monthly_budget_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("50"))
    default_job_budget_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("5"))
    # Money held for in-flight paid calls across all jobs (see app.services.budget).
    reserved_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    allow_cloud_fallback: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_external_ai_for_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    business_context: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # owner | admin | member
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ApiKey(Base):
    """Service credentials (n8n, scripts). Only a SHA-256 hash of the key is stored."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # What the key may do (see app.deps.SCOPES). NULL: a key created before scopes existed, which keeps the
    # access it had (jobs and Quote-to-Cash reads). New keys get only the scopes they are created with.
    scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_project_org_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    brand_guidelines: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_job_org_idempotency"),
        Index("ix_jobs_org_status", "organization_id", "status"),
        Index("ix_jobs_org_created", "organization_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    command: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.QUEUED)
    demo_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    planner_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="th")
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="16:9")
    data_classification: Mapped[str] = mapped_column(String(16), default="internal")
    requested_agents: Mapped[list[str]] = mapped_column(JSON, default=list)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Structured request details beside the free-text command, e.g. {"poster": {...}} for event posters.
    options: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    budget_limit_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("5"))
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    actual_cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    qa_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    qa_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    qa_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    qa_revisions: Mapped[int] = mapped_column(Integer, default=0)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    auto_start: Mapped[bool] = mapped_column(Boolean, default=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    tasks: Mapped[list[Task]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "key", name="uq_task_job_key"),
        Index("ix_tasks_status_lease", "status", "lease_expires_at"),
        Index("ix_tasks_org", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.PENDING)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_location: Mapped[str | None] = mapped_column(String(8), nullable=True)
    routing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    actual_cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    queued_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="tasks")


class TaskDependency(Base):
    """task_id waits for depends_on_task_id.

    soft=False: the dependency must succeed, otherwise the dependent task is skipped.
    soft=True: the dependent only waits until the dependency is terminal (any outcome).
    """

    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    soft: Mapped[bool] = mapped_column(Boolean, default=False)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_org_created", "organization_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    text_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class BrandAsset(Base):
    """A logo or other image the organization uploaded once and reuses (e.g. sponsor logos on posters)."""

    __tablename__ = "brand_assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # "logo" (event, sponsor and presenter logos) or "artwork" (a full poster background the user made,
    # e.g. in Adobe Firefly or Photoshop, used instead of AI-generated artwork).
    kind: Mapped[str] = mapped_column(String(16), default="logo", server_default="logo")
    # "upload" (the user's own file) or "ai" (the artwork of an AI-made poster, kept so the poster can be
    # re-laid out later - new text, logos or QR - without paying for new artwork).
    source: Mapped[str] = mapped_column(String(16), default="upload", server_default="upload")
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    content_type: Mapped[str] = mapped_column(String(100), default="image/png")
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class WorkerHeartbeat(Base):
    """A Celery worker process saying "I'm alive" every 20 s. Works with every broker (Celery's own
    inspect/broadcast does not work on the filesystem development broker)."""

    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(200), primary_key=True)  # e.g. "celery@host:12345"
    queues: Mapped[list] = mapped_column(JSON, default=list)
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_org_created", "organization_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    agent: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(32))
    units: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_provider_org_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))  # openai | comfyui
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_org_job_created", "organization_id", "job_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True)
    task_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str] = mapped_column(String(340))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# Quote-to-Cash tables live in their own module; importing it here registers them on Base.metadata for
# Alembic and the test suite (they only reference organizations, so there is no import cycle).
from app.qtc import models as _qtc_models  # noqa: E402, F401
