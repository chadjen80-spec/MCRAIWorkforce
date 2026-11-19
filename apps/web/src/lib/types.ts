// Types mirroring docs/API.md. Keep this file in sync with the backend contract.

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

// ---- Auth ------------------------------------------------------------------

export type Role = "owner" | "admin" | "member";

export interface User {
  id: string;
  email: string;
  full_name: string;
}

export interface OrganizationRef {
  id: string;
  name: string;
  slug: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
  organization: OrganizationRef;
  role: Role;
}

/** What the web app returns to the browser (never the token). Same as GET /v1/auth/me. */
export interface Session {
  user: User;
  organization: OrganizationRef;
  role: Role;
}

// ---- Jobs ------------------------------------------------------------------

export const JOB_STATUSES = [
  "queued",
  "planning",
  "planned",
  "running",
  "awaiting_approval",
  "completed",
  "partial",
  "failed",
  "cancelling",
  "cancelled",
  "rejected",
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export const TERMINAL_JOB_STATUSES: readonly JobStatus[] = [
  "completed",
  "partial",
  "failed",
  "cancelled",
  "rejected",
];

export const REQUESTABLE_AGENTS = ["content", "image", "document", "video"] as const;
export type RequestableAgent = (typeof REQUESTABLE_AGENTS)[number];

export type Language = "th" | "en";
export type AspectRatio = "16:9" | "9:16";
export const DATA_CLASSIFICATIONS = ["public", "internal", "restricted"] as const;
export type DataClassification = (typeof DATA_CLASSIFICATIONS)[number];
export type QaVerdict = "PASS" | "REVISE" | "HUMAN_REVIEW";
export type ApprovalStatus = "pending" | "approved" | "rejected";

export const TASK_STATUSES = [
  "pending",
  "queued",
  "running",
  "succeeded",
  "failed",
  "skipped",
  "cancelled",
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export interface Job {
  id: string;
  organization_id: string;
  project_id: string | null;
  command: string;
  status: JobStatus;
  demo_mode: boolean;
  planner_mode: "llm" | "rules" | null;
  language: Language;
  aspect_ratio: AspectRatio;
  data_classification: DataClassification;
  requested_agents: RequestableAgent[];
  budget_limit_usd: number | null;
  estimated_cost_usd: number | null;
  actual_cost_usd: number | null;
  progress: number;
  task_counts: Record<TaskStatus, number>;
  qa_verdict: QaVerdict | null;
  qa_summary: string | null;
  require_approval: boolean;
  approval_status: ApprovalStatus | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface JobDetail extends Job {
  plan: Record<string, unknown> | null;
}

export interface CreateJobRequest {
  command: string;
  agents?: RequestableAgent[];
  budget_limit_usd?: number;
  language?: Language;
  aspect_ratio?: AspectRatio;
  require_approval?: boolean;
  data_classification?: DataClassification;
  project_id?: string | null;
  auto_start?: boolean;
  poster?: PosterSpec;
}

export const POSTER_THEMES = ["gold", "neon", "crimson"] as const;
export type PosterTheme = (typeof POSTER_THEMES)[number];

/** An event poster: AI paints the artwork, the app typesets the text and the organization's logos. */
export type PosterTemplate = "esports" | "tournament";

/** One column of the tournament poster's schedule card. */
export interface ScheduleItem {
  label: string;
  big: string;
  lines: string[];
}

export interface PosterSpec {
  template?: PosterTemplate;
  theme: PosterTheme;
  tag: string;
  title: string;
  edition: string;
  date_text: string;
  headline: string[];
  venue: string;
  time_text: string;
  art: string;
  /** Your own artwork from the library instead of AI-generated artwork. */
  art_asset_id?: string | null;
  event_logo_id: string | null;
  sponsor_logo_ids: string[];
  presenter_label: string;
  presenter_logo_ids: string[];
  variants: number;
  /** Artwork placement, usually set by chatting with Mira: zoom 1 fills the frame; x / y move it
   * by a share of the frame (positive = right / down). */
  art_zoom?: number;
  art_x?: number;
  art_y?: number;
  // Tournament template (4:5) only.
  game_logo_id?: string | null;
  subtitle?: string;
  prize_label?: string;
  prize_amount?: string;
  registration_text?: string;
  badge_text?: string;
  category_lines?: string[];
  schedule?: ScheduleItem[];
  cta_text?: string;
  qr_url?: string;
  venue_detail?: string;
  venue_photo_id?: string | null;
  /** Characters standing left (blue side) and right (red side) of the orb; no AI art when set. */
  hero_left_id?: string | null;
  hero_right_id?: string | null;
}

/** A logo in the organization's logo library. */
export interface BrandAsset {
  id: string;
  name: string;
  kind: "logo" | "artwork";
  /** "ai": artwork kept from an AI-made poster (reuse it to re-lay out the poster for free). */
  source?: "upload" | "ai";
  content_type: string;
  width: number;
  height: number;
  size_bytes: number;
  url: string;
  created_at: string;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ItemList<T> {
  items: T[];
}

export const AGENTS = ["planner", "content", "image", "document", "video", "qa"] as const;
export type Agent = (typeof AGENTS)[number];

export interface Task {
  id: string;
  job_id: string;
  key: string;
  agent: Agent;
  title: string;
  status: TaskStatus;
  provider: string | null;
  execution_location: "cloud" | "local" | null;
  routing_reason: string | null;
  attempt: number;
  max_attempts: number;
  depends_on: string[];
  params: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  estimated_cost_usd: number | null;
  actual_cost_usd: number | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export const ARTIFACT_KINDS = [
  "image",
  "caption",
  "copy",
  "document_pdf",
  "document_docx",
  "video",
  "subtitle",
  "audio",
  "script",
  "storyboard",
  "manifest",
  "qa_report",
] as const;
export type ArtifactKind = (typeof ARTIFACT_KINDS)[number];

export interface Artifact {
  id: string;
  job_id: string;
  task_id: string;
  task_key: string;
  agent: Agent;
  kind: ArtifactKind;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  is_demo: boolean;
  text_preview: string | null;
  metadata: Record<string, unknown>;
  /** Relative, HMAC-signed, expiring URL. Always go through fileUrl(). */
  /** False while the job awaits approval or was rejected: a draft for review, not for delivery. */
  released?: boolean;
  download_url: string;
  created_at: string;
}

export interface JobEvent {
  id: string;
  at: string;
  action: string;
  actor: string;
  task_key: string | null;
  detail: Record<string, unknown>;
}

export interface ApprovalRequest {
  decision: "approved" | "rejected";
  comment?: string;
}

export interface QaCheck {
  name: string;
  status: "pass" | "fail" | "warn";
  detail: string;
}

export interface QaReport {
  verdict: QaVerdict | null;
  summary: string | null;
  checks: QaCheck[];
}

// ---- Dashboard / usage -----------------------------------------------------

export interface DashboardSummary {
  active_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  awaiting_approval_jobs: number;
  month_cost_usd: number;
  active_estimated_cost_usd: number;
  monthly_budget_usd: number;
  recent_artifacts: Artifact[];
  /** Agent queues with no live background worker; new work there waits until one starts. */
  offline_queues?: string[];
}

export interface Usage {
  period: { from: string; to: string };
  total_cost_usd: number;
  monthly_budget_usd: number;
  by_provider: { provider: string; cost_usd: number; events: number }[];
  by_agent: { agent: string; cost_usd: number; events: number }[];
  daily: { date: string; cost_usd: number }[];
}

// ---- Settings --------------------------------------------------------------

export interface OrganizationSettings {
  id: string;
  name: string;
  slug: string;
  monthly_budget_usd: number;
  default_job_budget_usd: number;
  allow_cloud_fallback: boolean;
  allow_external_ai_for_restricted: boolean;
  business_context: string | null;
}

export type OrganizationPatch = Partial<
  Pick<
    OrganizationSettings,
    | "name"
    | "monthly_budget_usd"
    | "default_job_budget_usd"
    | "allow_cloud_fallback"
    | "allow_external_ai_for_restricted"
  > & { business_context: string }
>;

export interface Provider {
  name: string;
  kind: "llm" | "image" | "tts";
  location: "cloud" | "local";
  configured: boolean;
  enabled: boolean;
  healthy: boolean;
  detail: string | null;
  /** Non-secret settings shown in the form (Firefly: priority, model, content_class, masked client_id). */
  options?: Record<string, string>;
}

/** Providers that can be configured through PUT /v1/settings/providers/{name}. */
export const CONFIGURABLE_PROVIDERS: readonly string[] = ["openai", "comfyui", "firefly"];

export const FIREFLY_MODELS = ["", "image3", "image4_standard", "image4_ultra"] as const;

export interface ProviderUpdate {
  enabled?: boolean;
  /** For Adobe Firefly this is the Client Secret. */
  api_key?: string;
  base_url?: string;
  model?: string;
  client_id?: string;
  priority?: "first" | "fallback";
  content_class?: "art" | "photo";
  /** OpenAI only: generate images before ComfyUI ("first") or only as a fallback. */
  image_priority?: "first" | "fallback";
}

export interface WorkerStatus {
  queue: string;
  online: boolean;
  workers: number;
}

export interface Settings {
  organization: OrganizationSettings;
  providers: Provider[];
  workers: WorkerStatus[];
  demo_mode_enabled: boolean;
}
