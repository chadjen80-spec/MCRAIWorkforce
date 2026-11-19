// Authenticated passthrough to the backend API. The JWT lives only in the
// httpOnly session cookie; it is attached here, on the server.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  SESSION_COOKIE,
  apiBaseUrl,
  clearSessionCookie,
  errorResponse,
  safeUpstreamPath,
  upstreamUnavailable,
} from "@/lib/server";

type Ctx = { params: Promise<{ path: string[] }> };

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "idempotency-key"];

async function forward(request: NextRequest, ctx: Ctx): Promise<NextResponse> {
  const { path } = await ctx.params;
  const upstreamPath = safeUpstreamPath(path);
  if (!upstreamPath || path[0] !== "v1") {
    return errorResponse(404, "NOT_FOUND", "Unknown API path.");
  }

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return errorResponse(401, "UNAUTHENTICATED", "Not signed in.");
  }

  const headers = new Headers({ Authorization: `Bearer ${token}` });
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let body: ArrayBuffer | undefined;
  if (hasBody) {
    const buf = await request.arrayBuffer();
    if (buf.byteLength > 0) body = buf;
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${apiBaseUrl()}/${upstreamPath}${request.nextUrl.search}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return upstreamUnavailable();
  }

  const resHeaders = new Headers({ "Cache-Control": "no-store" });
  const contentType = upstream.headers.get("content-type");
  if (contentType) resHeaders.set("Content-Type", contentType);

  const noBody = upstream.status === 204 || upstream.status === 304 || request.method === "HEAD";
  const res = new NextResponse(noBody ? null : await upstream.arrayBuffer(), {
    status: upstream.status,
    headers: resHeaders,
  });
  if (upstream.status === 401) clearSessionCookie(res);
  return res;
}

export const GET = forward;
export const HEAD = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
