"""Application settings. Everything is driven by environment variables (see .env.example)."""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-change-me"  # noqa: S105 - rejected in production by _finalize()

AGENT_QUEUES = ("planner", "content", "image", "document", "video", "qa")
MAINTENANCE_QUEUE = "maintenance"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"  # development | test | production
    log_level: str = "INFO"
    log_json: bool = True
    data_dir: Path = Path("./data")

    # --- persistence -----------------------------------------------------
    database_url: str = "sqlite:///./data/mcr.db"

    # --- background processing -------------------------------------------
    # "filesystem://" needs no Redis (single-machine dev). Production: redis://redis:6379/0
    celery_broker_url: str = "filesystem://"
    celery_task_always_eager: bool = False
    redis_url: str | None = None  # used for rate limiting when set
    task_max_attempts: int = 3
    task_lease_seconds: int = 300
    task_queue_redelivery_seconds: int = 900
    retry_base_seconds: int = 5
    retry_max_seconds: int = 300
    qa_max_revisions: int = 1

    # --- storage -----------------------------------------------------------
    storage_backend: str = "local"  # local | s3
    storage_local_dir: Path | None = None
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "mcr-artifacts"
    s3_region: str = "us-east-1"

    # --- security ----------------------------------------------------------
    jwt_secret: str = _DEV_SECRET
    jwt_expires_minutes: int = 720
    artifact_url_secret: str | None = None
    artifact_url_ttl_seconds: int = 900
    secrets_encryption_key: str | None = None  # Fernet key; required to store provider keys in DB
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_minute: int = 240
    login_rate_limit_per_minute: int = 10

    # --- behaviour -----------------------------------------------------------
    # Explicit demo mode: when an AI provider is not configured, agents may emit clearly
    # labelled demo output (is_demo=true) instead of failing. Never enabled implicitly.
    demo_mode: bool = False
    # Quote-to-Cash: tax invoices and receipts (ใบกำกับภาษี / ใบเสร็จรับเงิน) are NOT issued by the system until
    # Thai legal and accounting requirements (Revenue Code formats, numbering, e-Tax rules) are verified.
    qtc_tax_documents_enabled: bool = False
    # Signed Quote-to-Cash events to n8n. Each event type is POSTed to f"{qtc_events_url}-{slug}", e.g.
    # http://n8n:5678/webhook/mcr-qtc-quotation-submitted. Nothing is delivered until both are set.
    qtc_events_url: str = ""
    qtc_events_secret: str = ""  # HMAC-SHA256 key, at least 32 characters; n8n reads the same value from its env
    qtc_due_soon_days: int = 3
    # --- MCR Smart Café (internet café / gaming centre) -------------------------
    # Off by default: every /v1/cafe route answers 404 FEATURE_DISABLED and Mira ignores café commands.
    cafe_enabled: bool = False
    # Modules switched on when cafe_enabled. Unfinished ones (pos, members, printing, reports) stay off.
    cafe_modules: str = "devices,sessions,launcher,assistant,assist,orders"
    # ECDSA P-256 private key (PEM) that signs seat commands. Required in production; development generates
    # DATA_DIR/cafe_command_signing_key.pem once.
    cafe_command_signing_key: str = ""
    # Separate secret for device tokens; blank derives one from JWT_SECRET with a distinct label.
    cafe_device_jwt_secret: str = ""
    cafe_device_token_minutes: int = 15
    cafe_device_online_seconds: int = 30
    cafe_command_ttl_seconds: int = 60
    cafe_launch_wait_seconds: float = 8
    cafe_enrollment_code_minutes: int = 15
    default_job_budget_usd: Decimal = Decimal("5")
    default_monthly_budget_usd: Decimal = Decimal("50")

    # --- cloud AI (OpenAI-compatible) ---------------------------------------
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    # Captions can use their own model (e.g. a stronger one for Thai copy); empty uses OPENAI_MODEL.
    openai_content_model: str = ""
    # Send rules as a "developer" message (OpenAI); falls back to the system message if a provider rejects it.
    openai_developer_role: bool = True
    # Full rewrites allowed when a caption fails the quality checks (per generation or regeneration).
    caption_max_rewrites: int = 1
    openai_image_model: str = "gpt-image-2"  # gpt-image-1 was retired; Settings → openai shows the one in use
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    # Voice of the dashboard assistant (a female-sounding provider voice) and its request cap.
    assistant_tts_voice: str = "nova"
    assistant_speech_per_minute: int = 40
    openai_timeout_seconds: float = 120
    # Price table used for cost tracking/budget enforcement. Verify against your provider's
    # current price list; these are configuration, not facts.
    llm_input_usd_per_1m: Decimal = Decimal("0.15")
    llm_output_usd_per_1m: Decimal = Decimal("0.60")
    # Per-model prices (USD per 1M tokens: [input, output]) for models other than the default above, e.g. the
    # caption model. A model not listed here is costed with the default prices. JSON in the environment:
    # LLM_MODEL_PRICES='{"gpt-4.1": [2.0, 8.0]}'
    llm_model_prices: dict[str, list[Decimal]] = Field(
        default_factory=lambda: {
            "gpt-4o-mini": [Decimal("0.15"), Decimal("0.60")],
            "gpt-4.1-mini": [Decimal("0.40"), Decimal("1.60")],
            "gpt-4.1": [Decimal("2.00"), Decimal("8.00")],
            "gpt-4o": [Decimal("2.50"), Decimal("10.00")],
        }
    )
    image_usd_per_image: Decimal = Decimal("0.07")
    tts_usd_per_1k_chars: Decimal = Decimal("0.015")
    llm_estimated_usd_per_call: Decimal = Decimal("0.01")

    # --- local AI -------------------------------------------------------------
    # Adobe Firefly Services (cloud image generation). Credentials from an Adobe Developer Console
    # project with Firefly Services (server-to-server OAuth). Usually set in Settings instead.
    firefly_client_id: str | None = None
    firefly_client_secret: str | None = None
    firefly_model_version: str = ""  # x-model-version header; blank = Adobe's default (image3)
    firefly_api_url: str = "https://firefly-api.adobe.io"
    firefly_ims_url: str = "https://ims-na1.adobelogin.com/ims/token/v3"
    firefly_scopes: str = "openid,AdobeID,read_organizations,firefly_enterprise,firefly_api,ff_apis"
    firefly_timeout_seconds: int = 180
    # Estimate only: Adobe bills in "operations" set by your contract. Used for budgets and usage reports.
    firefly_usd_per_image: Decimal = Decimal("0.04")
    comfyui_url: str | None = None  # e.g. http://127.0.0.1:8188
    comfyui_checkpoint: str = "sd_xl_base_1.0.safetensors"
    comfyui_workflow_path: Path | None = None  # optional custom API-format workflow JSON
    comfyui_timeout_seconds: int = 600
    comfyui_max_queue: int = 4
    comfyui_steps: int = 25

    # --- media ------------------------------------------------------------------
    ffmpeg_path: str | None = None
    font_regular_path: str | None = None
    font_bold_path: str | None = None
    font_display_path: str | None = None  # heavy headline font for posters (default: Kanit Black Italic if installed)
    video_fps: int = 30
    ffmpeg_timeout_seconds: int = 900

    # --- bootstrap (python -m app.cli seed) ----------------------------------
    bootstrap_org_name: str = "MCR Technology"
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None

    # ---------------------------------------------------------------------------
    brand: dict = Field(
        default_factory=lambda: {
            "product": "MCR SME Copilot",
            "company": "MCR Technology",
            "primary_color": "#2563EB",
            "accent_color": "#22D3EE",
            "background_color": "#0B1220",
        }
    )

    @model_validator(mode="after")
    def _finalize(self) -> Settings:
        if self.storage_local_dir is None:
            self.storage_local_dir = self.data_dir / "artifacts"
        if not self.artifact_url_secret:
            self.artifact_url_secret = self.jwt_secret + ":artifact"
        if self.environment == "production":
            problems = []
            if self.jwt_secret == _DEV_SECRET or len(self.jwt_secret) < 32:
                problems.append("JWT_SECRET must be a random string of at least 32 characters")
            if not self.secrets_encryption_key:
                problems.append("SECRETS_ENCRYPTION_KEY is required")
            if self.database_url.startswith("sqlite"):
                problems.append("DATABASE_URL must point to PostgreSQL")
            if self.celery_broker_url.startswith("filesystem"):
                problems.append("CELERY_BROKER_URL must point to Redis")
            if self.celery_task_always_eager:
                problems.append("CELERY_TASK_ALWAYS_EAGER must be false")
            if self.cafe_enabled and not self.cafe_command_signing_key:
                problems.append("CAFE_COMMAND_SIGNING_KEY is required when CAFE_ENABLED is true")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def cafe_module_set(self) -> frozenset[str]:
        if not self.cafe_enabled:
            return frozenset()
        return frozenset(m.strip() for m in self.cafe_modules.split(",") if m.strip())

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
