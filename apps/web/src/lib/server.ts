// Server-only helpers shared by the route handlers and proxy.ts.
// Never import this file from a client component.

import { NextResponse } from "next/server";
import type { ApiErrorBody } from "./types";

export const SESSION_COOKIE = "mcr_session";

export function apiBaseUrl(): string {
  return (process.env.API_INTERNAL_URL || "http://localhost:8000").replace(/\/+$/, "");
}

export function sessionCookieOptions(maxAgeSeconds: number) {
  const override = process.env.SESSION_COOKIE_SECURE;
  const secure = override ? override !== "false" : process.env.NODE_ENV === "production";
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

export function clearSessionCookie(res: NextResponse): void {
  res.cookies.set(SESSION_COOKIE, "", sessionCookieOptions(0));
}

/** Error response in the same shape the backend uses. */
export function errorResponse(status: number, code: string, message: string): NextResponse {
  const body: ApiErrorBody = { error: { code, message } };
  return NextResponse.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

export function upstreamUnavailable(): NextResponse {
  return errorResponse(502, "UPSTREAM_UNAVAILABLE", "The API server could not be reached.");
}

/**
 * Validates catch-all path segments and returns a safely encoded upstream path
 * ("v1/jobs/123"), or null when a segment could escape the intended prefix.
 */
export function safeUpstreamPath(segments: string[]): string | null {
  if (segments.length === 0) return null;
  for (const s of segments) {
    if (s === "" || s === "." || s === ".." || /[\\/\0?#]/.test(s)) return null;
  }
  return segments.map(encodeURIComponent).join("/");
}
