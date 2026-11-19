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

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "idempotency-key", "range", "if-range"];
// Passed back so file previews (drafts) can stream and seek like the signed-file route.
const FORWARDED_RESPONSE_HEADERS = ["content-type", "content-length", "content-range", "accept-ranges", "content-disposition"];

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

  const resHeaders = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) resHeaders.set(name, value);
  }

  const noBody = upstream.status === 204 || upstream.status === 304 || request.method === "HEAD";
  // Stream the body instead of buffering it (large previews, video seeking).
  const res = new NextResponse(noBody ? null : upstream.body, {
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
