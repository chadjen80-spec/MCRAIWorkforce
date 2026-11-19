from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Artifact, Job, Task, TaskStatus
from app.security import sign_artifact_url

AgentName = Literal["content", "image", "document", "video"]
ALL_AGENTS: list[str] = ["content", "image", "document", "video"]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str


class OrgBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    user: UserOut
    organization: OrgBrief
    role: str


class MeResponse(BaseModel):
    user: UserOut | None
    organization: OrgBrief
    role: str


def _clean(v: str) -> str:
    return " ".join(v.split())


class ScheduleItem(BaseModel):
    """One column of the tournament poster's schedule card, e.g. "แข่งขัน" / "8-9" / ["สิงหาคม", "2569"]."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=40)
    big: str = Field(default="", max_length=8)
    lines: list[str] = Field(default_factory=list, max_length=2)

    @field_validator("label", "big")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _clean(v)

    @field_validator("lines")
    @classmethod
    def _lines(cls, v: list[str]) -> list[str]:
        lines = [_clean(x) for x in v if x.strip()]
        if any(len(x) > 24 for x in lines):
            raise ValueError("each schedule line must be at most 24 characters")
        return lines


class PosterSpec(BaseModel):
    """An event poster laid out by the app: AI paints the artwork, the text and logos are typeset exactly."""

    model_config = ConfigDict(extra="forbid")

    # esports: 9:16 key-art poster. tournament: 4:5 poster with prize, schedule card, QR code and sponsor strip.
    template: Literal["esports", "tournament"] = "esports"
    theme: Literal["gold", "neon", "crimson"] = "gold"
    tag: str = Field(default="", max_length=24)  # small line above the title, e.g. the game name
    title: str = Field(min_length=1, max_length=40)
    edition: str = Field(default="", max_length=12)  # e.g. the year
    date_text: str = Field(default="", max_length=60)
    headline: list[str] = Field(default_factory=list, max_length=3)
    venue: str = Field(default="", max_length=80)
    time_text: str = Field(default="", max_length=40)
    art: str = Field(default="", max_length=400)  # description of the original artwork
    # Your own artwork from the library (kind "artwork") instead of AI-generated artwork.
    art_asset_id: uuid.UUID | None = None
    event_logo_id: uuid.UUID | None = None
    sponsor_logo_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)
    presenter_label: str = Field(default="Presented by", max_length=40)
    presenter_logo_ids: list[uuid.UUID] = Field(default_factory=list, max_length=6)
    variants: int = Field(default=1, ge=1, le=3)
    # Where the artwork sits: zoom 1 = fills the frame; x / y move it by a share of the frame
    # (positive = right / down). Set by chatting ("move the character down", "make it bigger").
    art_zoom: float = Field(default=1.0, ge=0.5, le=2.5)
    art_x: float = Field(default=0.0, ge=-0.6, le=0.6)
    art_y: float = Field(default=0.0, ge=-0.6, le=0.6)
    # --- tournament template only ---
    game_logo_id: uuid.UUID | None = None  # the game's logo, centred over the artwork
    subtitle: str = Field(default="", max_length=90)  # e.g. the project name
    prize_label: str = Field(default="", max_length=40)  # e.g. "ชิงเงินรางวัลรวมกว่า"
    prize_amount: str = Field(default="", max_length=24)  # e.g. "8,000 บาท"
    registration_text: str = Field(default="", max_length=80)
    badge_text: str = Field(default="", max_length=40)  # red pill, e.g. "สมัครฟรี! ไม่มีค่าใช้จ่าย"
    category_lines: list[str] = Field(default_factory=list, max_length=2)  # *text* is highlighted
    schedule: list[ScheduleItem] = Field(default_factory=list, max_length=2)
    cta_text: str = Field(default="", max_length=20)  # above the QR code, e.g. "สมัครเลย"
    qr_url: str = Field(default="", max_length=300)  # the registration link, drawn as a QR code
    venue_detail: str = Field(default="", max_length=80)
    venue_photo_id: uuid.UUID | None = None  # a photo of the venue from the artwork library
    # Characters (cut-out PNGs work best) standing left and right of the orb. With either one set, the
    # app draws the space backdrop itself: no AI image is generated.
    hero_left_id: uuid.UUID | None = None
    hero_right_id: uuid.UUID | None = None

    @field_validator("tag", "title", "edition", "date_text", "venue", "time_text", "art", "presenter_label",
                     "subtitle", "prize_label", "prize_amount", "registration_text", "badge_text", "cta_text",
                     "qr_url", "venue_detail")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _clean(v)

    @field_validator("headline")
    @classmethod
    def _lines(cls, v: list[str]) -> list[str]:
        lines = [_clean(x) for x in v if x.strip()]
        if any(len(x) > 48 for x in lines):
            raise ValueError("each headline line must be at most 48 characters")
        return lines

    @field_validator("category_lines")
    @classmethod
    def _category(cls, v: list[str]) -> list[str]:
        lines = [_clean(x) for x in v if x.strip()]
        if any(len(x) > 60 for x in lines):
            raise ValueError("each category line must be at most 60 characters")
        return lines

    @field_validator("qr_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if v and not v.lower().startswith(("https://", "http://")):
            raise ValueError("the registration link must start with https://")
        return v

    def logo_ids(self) -> list[uuid.UUID]:
        ids = [self.event_logo_id, self.game_logo_id, self.hero_left_id, self.hero_right_id,
               *self.sponsor_logo_ids, *self.presenter_logo_ids]
        return [i for i in ids if i]


class AssetOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str = "logo"
    source: str = "upload"
    content_type: str
    width: int
    height: int
    size_bytes: int
    url: str
    created_at: datetime


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=5, max_length=4000)
    agents: list[AgentName] | None = None
    budget_limit_usd: Decimal | None = Field(default=None, gt=0, le=1000)
    language: Literal["th", "en"] = "th"
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    require_approval: bool = True
    data_classification: Literal["public", "internal", "restricted"] = "internal"
    project_id: uuid.UUID | None = None
    auto_start: bool = True
    # An event poster: the job then makes only the poster(s), in 9:16.
    poster: PosterSpec | None = None

    @field_validator("command")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError("command must be at least 5 characters")
        return v

    @field_validator("agents")
    @classmethod
    def _agents(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and not v:
            raise ValueError("select at least one agent")
        return list(dict.fromkeys(v)) if v else v


class JobOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID
    command: str
    status: str
    demo_mode: bool
    planner_mode: str | None
    language: str
    aspect_ratio: str
    data_classification: str
    requested_agents: list[str]
    budget_limit_usd: float
    estimated_cost_usd: float
    actual_cost_usd: float
    progress: int
    task_counts: dict[str, int]
    qa_verdict: str | None
    qa_summary: str | None
    require_approval: bool
    approval_status: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class JobDetail(JobOut):
    plan: dict[str, Any] | None
    options: dict[str, Any] | None = None


def job_out(job: Job, counts: dict[str, int], *, detail: bool = False) -> JobOut:
    full = {s: int(counts.get(s, 0)) for s in sorted(TaskStatus.ALL)}
    total = sum(full.values())
    done = sum(full[s] for s in TaskStatus.TERMINAL)
    if job.status in ("completed", "partial", "failed", "cancelled", "rejected", "awaiting_approval"):
        progress = 100
    else:
        progress = int(100 * done / total) if total else 0
    data: dict[str, Any] = dict(
        id=job.id, organization_id=job.organization_id, project_id=job.project_id, command=job.command,
        status=job.status, demo_mode=job.demo_mode, planner_mode=job.planner_mode, language=job.language,
        aspect_ratio=job.aspect_ratio, data_classification=job.data_classification,
        requested_agents=job.requested_agents or [], budget_limit_usd=float(job.budget_limit_usd),
        estimated_cost_usd=float(job.estimated_cost_usd), actual_cost_usd=float(job.actual_cost_usd),
        progress=progress, task_counts=full, qa_verdict=job.qa_verdict, qa_summary=job.qa_summary,
        require_approval=job.require_approval, approval_status=job.approval_status, error_code=job.error_code,
        error_message=job.error_message, created_at=job.created_at, started_at=job.started_at,
        finished_at=job.finished_at, updated_at=job.updated_at,
    )
    return JobDetail(**data, plan=job.plan, options=job.options) if detail else JobOut(**data)


class JobList(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class TaskOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    key: str
    agent: str
    title: str
    status: str
    provider: str | None
    execution_location: str | None
    routing_reason: str | None
    attempt: int
    max_attempts: int
    depends_on: list[str]
    params: dict[str, Any]
    error_code: str | None
    error_message: str | None
    estimated_cost_usd: float
    actual_cost_usd: float
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


def task_out(task: Task, depends_on: list[str]) -> TaskOut:
    return TaskOut(
        id=task.id, job_id=task.job_id, key=task.key, agent=task.agent, title=task.title, status=task.status,
        provider=task.provider, execution_location=task.execution_location, routing_reason=task.routing_reason,
        attempt=task.attempt, max_attempts=task.max_attempts, depends_on=sorted(depends_on), params=task.params or {},
        error_code=task.error_code, error_message=task.error_message,
        estimated_cost_usd=float(task.estimated_cost_usd), actual_cost_usd=float(task.actual_cost_usd),
        queued_at=task.queued_at, started_at=task.started_at, finished_at=task.finished_at,
    )


class TaskList(BaseModel):
    items: list[TaskOut]


class ArtifactOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    task_id: uuid.UUID
    task_key: str
    agent: str
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    is_demo: bool
    text_preview: str | None
    metadata: dict[str, Any]
    # Released = the job finished and, when approval was required, was approved. Unreleased files are
    # drafts: people in the dashboard can still review them, but automations (API keys) get no link.
    released: bool = True
    download_url: str | None
    created_at: datetime


# Job states whose files may be delivered: approved/auto-completed, or partial (final, no approval step).
RELEASED_JOB_STATUSES = frozenset({"completed", "partial"})


def artifact_out(art: Artifact, task_key: str, agent: str, *, released: bool = True,
                 link: bool = True) -> ArtifactOut:
    meta = {k: v for k, v in (art.meta or {}).items() if k != "prompt_id"}
    return ArtifactOut(
        id=art.id, job_id=art.job_id, task_id=art.task_id, task_key=task_key, agent=agent, kind=art.kind,
        filename=art.filename, content_type=art.content_type, size_bytes=art.size_bytes, sha256=art.sha256,
        is_demo=art.is_demo, text_preview=art.text_preview, metadata=meta, released=released,
        download_url=sign_artifact_url(art.id) if link else None, created_at=art.created_at,
    )


class ArtifactList(BaseModel):
    items: list[ArtifactOut]


class ArtifactPage(ArtifactList):
    total: int
    limit: int
    offset: int


class EventOut(BaseModel):
    id: uuid.UUID
    at: datetime
    action: str
    actor: str
    task_key: str | None
    detail: dict[str, Any]


class EventList(BaseModel):
    items: list[EventOut]


class EventCreate(BaseModel):
    action: str = Field(min_length=3, max_length=60, pattern=r"^[a-z0-9_.-]+$")
    detail: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str | None = Field(default=None, max_length=2000)


class BulkDeleteResult(BaseModel):
    deleted: int
    skipped_active: int
    files_removed: int


class ArtifactTextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20000)


class ImageRegenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Images: optional new scene description. The image model knows nothing about the product, so a
    # concrete scene ("a bakery owner using a tablet...") works far better than product talk.
    concept: str | None = Field(default=None, max_length=500)
    # Documents: optional instruction for this version ("focus on restaurants", "shorter").
    instruction: str | None = Field(default=None, max_length=500)


class CaptionRegenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str | None = Field(default=None, max_length=500)


class CaptionRegenerateResult(BaseModel):
    artifact: ArtifactOut
    warnings: list[str]


class ArtifactsDeleteResult(BaseModel):
    deleted: int
    skipped_active: int


class DispatchResponse(BaseModel):
    job: JobOut
    dispatched: list[str]


class QACheck(BaseModel):
    name: str
    status: str
    detail: str


class QAResponse(BaseModel):
    verdict: str | None
    summary: str | None
    checks: list[QACheck]


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    monthly_budget_usd: float
    default_job_budget_usd: float
    allow_cloud_fallback: bool
    allow_external_ai_for_restricted: bool
    business_context: str


class OrganizationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    monthly_budget_usd: Decimal | None = Field(default=None, ge=0, le=100000)
    default_job_budget_usd: Decimal | None = Field(default=None, gt=0, le=1000)
    allow_cloud_fallback: bool | None = None
    allow_external_ai_for_restricted: bool | None = None
    business_context: str | None = Field(default=None, max_length=20000)


class ProviderOut(BaseModel):
    name: str
    kind: str
    location: str
    configured: bool
    enabled: bool
    healthy: bool | None
    detail: str
    # Non-secret settings the form shows (e.g. Firefly's model, priority and a masked client id).
    options: dict[str, str] = Field(default_factory=dict)


class ProviderPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=400)  # Firefly: the Client Secret
    base_url: str | None = Field(default=None, max_length=300, pattern=r"^https?://")
    model: str | None = Field(default=None, max_length=120)
    # Adobe Firefly only
    client_id: str | None = Field(default=None, min_length=8, max_length=200)
    priority: Literal["first", "fallback"] | None = None
    content_class: Literal["art", "photo"] | None = None
    # OpenAI only: use its image model before ComfyUI ("first") or only as a fallback (the default)
    image_priority: Literal["first", "fallback"] | None = None


class WorkerOut(BaseModel):
    queue: str
    online: bool
    workers: int


class SettingsOut(BaseModel):
    organization: OrganizationOut
    providers: list[ProviderOut]
    workers: list[WorkerOut]
    demo_mode_enabled: bool


class DashboardSummary(BaseModel):
    active_jobs: int
    completed_jobs: int
    failed_jobs: int
    awaiting_approval_jobs: int
    month_cost_usd: float
    active_estimated_cost_usd: float
    monthly_budget_usd: float
    recent_artifacts: list[ArtifactOut]
    # Agent queues with no live worker (from heartbeats): new work there would wait. Empty = all fine.
    offline_queues: list[str] = []


class UsageBucket(BaseModel):
    cost_usd: float
    events: int


class ProviderUsage(UsageBucket):
    provider: str


class AgentUsage(UsageBucket):
    agent: str


class DailyUsage(BaseModel):
    date: str
    cost_usd: float


class UsageOut(BaseModel):
    period: dict[str, str]
    total_cost_usd: float
    monthly_budget_usd: float
    by_provider: list[ProviderUsage]
    by_agent: list[AgentUsage]
    daily: list[DailyUsage]
