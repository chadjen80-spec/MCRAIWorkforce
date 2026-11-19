// Browser-side API client. Every call goes to the same-origin /api/proxy route,
// which attaches the session token on the server. The browser never sees the JWT.

import type { ApiErrorBody, Artifact } from "./types";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/** Codes produced by this client (not by the API); the UI localizes their messages. */
export const CLIENT_ERROR_CODES = {
  network: "NETWORK_ERROR",
  badResponse: "BAD_RESPONSE",
} as const;

export function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError(0, CLIENT_ERROR_CODES.network, e instanceof Error ? e.message : String(e));
}

function isErrorBody(v: unknown): v is ApiErrorBody {
  if (typeof v !== "object" || v === null) return false;
  const err = (v as { error?: unknown }).error;
  return (
    typeof err === "object" &&
    err !== null &&
    typeof (err as { code?: unknown }).code === "string" &&
    typeof (err as { message?: unknown }).message === "string"
  );
}

async function parseResponse<T>(res: Response): Promise<T> {
  const text = await res.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = undefined;
    }
  }
  if (!res.ok) {
    if (isErrorBody(parsed)) {
      throw new ApiError(res.status, parsed.error.code, parsed.error.message, parsed.error.details);
    }
    throw new ApiError(res.status, `HTTP_${res.status}`, res.statusText || `HTTP ${res.status}`);
  }
  if (text && parsed === undefined) {
    throw new ApiError(res.status, CLIENT_ERROR_CODES.badResponse, "Response was not valid JSON.");
  }
  return parsed as T;
}

export interface ApiOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | undefined | null | string[]>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

function redirectToLogin(): void {
  if (typeof window === "undefined" || window.location.pathname === "/login") return;
  const next = window.location.pathname + window.location.search;
  // Deliberate full page load (this is not a component, and stale session state must go).
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

/** Call the backend, e.g. api<Job>("/v1/jobs/123"). Throws ApiError on any failure. */
export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((item) => params.append(k, item));
    else params.set(k, String(v));
  }
  const qs = params.toString();
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(`/api/proxy${path}${qs ? `?${qs}` : ""}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw toApiError(e);
  }
  if (res.status === 401) {
    // Session missing or expired: the proxy already cleared the cookie.
    redirectToLogin();
  }
  return parseResponse<T>(res);
}

/** Calls to this app's own /api/auth/* handlers. */
export async function authRequest<T>(path: "/api/auth/login" | "/api/auth/logout", body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body ?? {}),
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch (e) {
    throw toApiError(e);
  }
  return parseResponse<T>(res);
}

/**
 * Maps an artifact's relative signed `download_url` to the same-origin
 * streaming route, so <img>/<video>/<a download> work without exposing the API.
 */
export function fileUrl(downloadUrl: string): string {
  const path = downloadUrl.startsWith("/") ? downloadUrl : `/${downloadUrl}`;
  return `/api/files${path}`;
}

// The API signs a fresh URL on every listing. Re-using a recently seen URL per
// artifact keeps <img>/<video> sources stable across polls (no reload flicker),
// while still rotating well inside the signature lifetime (15 min by default).
const URL_REUSE_MS = 8 * 60 * 1000;
const urlCache = new Map<string, { url: string; at: number }>();

export function stabilizeArtifacts<T extends Artifact>(items: T[]): T[] {
  const now = Date.now();
  return items.map((a) => {
    const hit = urlCache.get(a.id);
    if (hit && now - hit.at < URL_REUSE_MS) {
      return hit.url === a.download_url ? a : { ...a, download_url: hit.url };
    }
    urlCache.set(a.id, { url: a.download_url, at: now });
    return a;
  });
}

export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // crypto.randomUUID needs a secure context; fall back for plain-HTTP LAN hosts.
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
