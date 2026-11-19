import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  SESSION_COOKIE,
  apiBaseUrl,
  errorResponse,
  sessionCookieOptions,
  upstreamUnavailable,
} from "@/lib/server";
import type { LoginResponse, Session } from "@/lib/types";

export async function POST(request: NextRequest) {
  let credentials: { email?: unknown; password?: unknown };
  try {
    credentials = await request.json();
  } catch {
    return errorResponse(422, "VALIDATION_ERROR", "Request body must be JSON.");
  }
  if (typeof credentials.email !== "string" || typeof credentials.password !== "string") {
    return errorResponse(422, "VALIDATION_ERROR", "Email and password are required.");
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${apiBaseUrl()}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ email: credentials.email, password: credentials.password }),
      cache: "no-store",
    });
  } catch {
    return upstreamUnavailable();
  }

  if (!upstream.ok) {
    // Pass the API's error body through unchanged ({error:{code,message}}).
    return new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  }

  let data: LoginResponse;
  try {
    data = (await upstream.json()) as LoginResponse;
  } catch {
    return errorResponse(502, "BAD_UPSTREAM_RESPONSE", "The API returned an unreadable login response.");
  }
  if (typeof data.access_token !== "string" || !data.access_token) {
    return errorResponse(502, "BAD_UPSTREAM_RESPONSE", "The API login response had no access token.");
  }

  const session: Session = { user: data.user, organization: data.organization, role: data.role };
  const res = NextResponse.json(session, { headers: { "Cache-Control": "no-store" } });
  const maxAge = Number.isFinite(data.expires_in) && data.expires_in > 0 ? Math.floor(data.expires_in) : 43200;
  res.cookies.set(SESSION_COOKIE, data.access_token, sessionCookieOptions(maxAge));
  return res;
}
