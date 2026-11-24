// MCR Smart Café: types and calls for /v1/cafe. Everything shown comes from the server; nothing is assumed.

import { api, newIdempotencyKey } from "@/lib/api";

export const CAFE_NAV_ENABLED = process.env.NEXT_PUBLIC_CAFE_ENABLED === "1" || process.env.NEXT_PUBLIC_CAFE_ENABLED === "true";

export type CafeModule = "devices" | "sessions" | "launcher" | "assistant" | "assist" | "orders" | "pos" | "members" | "printing" | "reports";

export interface CafeFeatures {
  enabled: boolean;
  modules: Record<CafeModule, boolean>;
}

export interface CafeDevice {
  id: string;
  name: string;
  online: boolean;
  client_version: string;
  os_version: string;
  last_heartbeat_at: string | null;
  installed_apps: Record<string, "installed" | "missing">;
}

export interface CafeSeat {
  id: string;
  label: string;
  zone: string;
  status: string;
  device: CafeDevice | null;
  session: { id: string; started_at: string; granted_seconds: number; remaining_seconds: number } | null;
  open_assists: number;
}

export interface CafeApp {
  id: string;
  app_key: string;
  name: string;
  aliases: string[];
  category: string;
  enabled: boolean;
  requires_session: boolean;
}

export interface CafeCommand {
  id: string;
  type: string;
  args: Record<string, unknown>;
  status: "queued" | "delivered" | "succeeded" | "failed" | "expired";
  result: { code?: string; message?: string; pid?: number };
  seat_label: string | null;
  requested_by: string;
  via: string;
  created_at: string;
  completed_at: string | null;
}

export interface CafeAssist {
  id: string;
  seat_label: string;
  message: string;
  source: string;
  status: string;
  created_at: string;
}

export interface CafeSessionRow {
  id: string;
  seat_label: string;
  status: string;
  granted_seconds: number;
  remaining_seconds: number;
  started_at: string;
  ended_at: string | null;
  customer_ref: string;
}

type Items<T> = { items: T[] };

export const cafeApi = {
  features: (signal?: AbortSignal) => api<CafeFeatures>("/v1/cafe/features", { signal }),
  seats: (signal?: AbortSignal) => api<Items<CafeSeat>>("/v1/cafe/seats", { signal }),
  createSeat: (label: string, zone: string) => api<CafeSeat>("/v1/cafe/seats", { method: "POST", body: { label, zone } }),
  enrollmentCode: (seatId: string) =>
    api<{ code: string; seat_label: string; expires_at: string }>(`/v1/cafe/seats/${seatId}/enrollment-code`, { method: "POST" }),
  revokeDevice: (deviceId: string) => api<{ status: string }>(`/v1/cafe/devices/${deviceId}/revoke`, { method: "POST" }),
  apps: (signal?: AbortSignal) => api<Items<CafeApp>>("/v1/cafe/apps", { signal }),
  createApp: (body: { app_key: string; name: string; aliases: string[]; category: string; requires_session: boolean }) =>
    api<CafeApp>("/v1/cafe/apps", { method: "POST", body }),
  patchApp: (id: string, body: { enabled?: boolean; requires_session?: boolean }) => api<CafeApp>(`/v1/cafe/apps/${id}`, { method: "PATCH", body }),
  startSession: (seatId: string, minutes: number) =>
    api<{ id: string; remaining_seconds: number }>(`/v1/cafe/seats/${seatId}/sessions`, { method: "POST", body: { minutes } }),
  endSession: (sessionId: string) => api<{ status: string }>(`/v1/cafe/sessions/${sessionId}/end`, { method: "POST" }),
  sessions: (status: "active" | "ended" | "all", signal?: AbortSignal) =>
    api<Items<CafeSessionRow>>("/v1/cafe/sessions", { query: { status }, signal }),
  launch: (seatId: string, appKey: string) =>
    api<CafeCommand>(`/v1/cafe/seats/${seatId}/launch`, {
      method: "POST",
      body: { app_key: appKey },
      headers: { "Idempotency-Key": newIdempotencyKey() },
    }),
  command: (id: string, signal?: AbortSignal) => api<CafeCommand>(`/v1/cafe/commands/${id}`, { signal }),
  commands: (signal?: AbortSignal) => api<Items<CafeCommand>>("/v1/cafe/commands", { query: { limit: 15 }, signal }),
  assists: (signal?: AbortSignal) => api<Items<CafeAssist>>("/v1/cafe/assist", { signal }),
  resolveAssist: (id: string) => api<{ status: string }>(`/v1/cafe/assist/${id}/resolve`, { method: "POST" }),
};

/** 3725 -> "1:02:05" */
export function formatRemaining(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** Wait for a launch command to finish (the seat PC answers), up to `timeoutMs`. Returns the last state seen. */
export async function waitForCommand(id: string, timeoutMs = 8000, everyMs = 500): Promise<CafeCommand> {
  const deadline = Date.now() + timeoutMs;
  let cmd = await cafeApi.command(id);
  while ((cmd.status === "queued" || cmd.status === "delivered") && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, everyMs));
    cmd = await cafeApi.command(id);
  }
  return cmd;
}
