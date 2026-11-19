// Next.js 16 renamed the `middleware` file convention to `proxy` (same behaviour).
//
// 1. Redirects visitors without a session cookie to /login for every app page.
//    The cookie's validity is checked by the API on each request; an expired or
//    forged token gets a 401 from /api/proxy, which clears it and sends the user here.
// 2. Streams signed artifact downloads (/api/files/v1/artifacts/<id>/download) from
//    the API. This is done here as a rewrite rather than in a Route Handler because
//    Next.js strips Content-Length from Route Handler responses, and media elements
//    and download progress want it alongside Range/Content-Range.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, apiBaseUrl, errorResponse } from "@/lib/server";

const FILES_PREFIX = "/api/files/";
const ARTIFACT_DOWNLOAD = /^\/api\/files\/v1\/artifacts\/([A-Za-z0-9_-]{1,128})\/download$/;
// Logos in the poster logo library (also signed, expiring links).
const ASSET_FILE = /^\/api\/files\/v1\/assets\/([A-Za-z0-9_-]{1,128})\/file$/;
// The signed URL is the authorization, so only range negotiation is sent upstream:
// no cookies (the session token must not leave this server) and nothing else from the browser.
const FILE_REQUEST_HEADERS = ["range", "if-range"];

function serveArtifact(request: NextRequest, hasSession: boolean): NextResponse {
  const artifact = ARTIFACT_DOWNLOAD.exec(request.nextUrl.pathname);
  const asset = artifact ? null : ASSET_FILE.exec(request.nextUrl.pathname);
  if ((!artifact && !asset) || (request.method !== "GET" && request.method !== "HEAD")) {
    return errorResponse(404, "NOT_FOUND", "Unknown file path.");
  }
  if (!hasSession) {
    // Only signed-in dashboard users may use this relay.
    return errorResponse(401, "UNAUTHENTICATED", "Not signed in.");
  }
  const headers = new Headers();
  for (const name of FILE_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const path = artifact ? `/v1/artifacts/${artifact[1]}/download` : `/v1/assets/${asset![1]}/file`;
  const target = new URL(`${apiBaseUrl()}${path}${request.nextUrl.search}`);
  return NextResponse.rewrite(target, { request: { headers } });
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const hasSession = Boolean(request.cookies.get(SESSION_COOKIE)?.value);

  if (pathname.startsWith(FILES_PREFIX)) return serveArtifact(request, hasSession);

  if (pathname === "/login") {
    if (hasSession) return NextResponse.redirect(new URL("/assistant", request.url));
    return NextResponse.next();
  }

  if (!hasSession) {
    const url = new URL("/login", request.url);
    if (pathname !== "/") url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }

  if (pathname === "/") return NextResponse.redirect(new URL("/assistant", request.url));
  return NextResponse.next();
}

export const config = {
  matcher: [
    // App pages: everything except route handlers, Next internals and static files.
    "/((?!api/|_next/static|_next/image|favicon.ico|.*\\.[a-zA-Z0-9]+$).*)",
    // Artifact file passthrough.
    "/api/files/:path*",
  ],
};
