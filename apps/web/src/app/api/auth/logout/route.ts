import { NextResponse } from "next/server";
import { clearSessionCookie } from "@/lib/server";

export async function POST() {
  const res = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  clearSessionCookie(res);
  return res;
}
