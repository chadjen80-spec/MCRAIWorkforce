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

// ---- food & drink orders ----------------------------------------------------------------------------------------
export interface MenuCategory {
  id: string;
  name: string;
  sort: number;
  active: boolean;
}

export interface MenuItem {
  id: string;
  category_id: string;
  name: string;
  description: string;
  aliases: string[];
  price: string; // exact decimal string from the server, e.g. "45.00"
  list_price: string | null; // the "was" price on a sale
  is_new: boolean;
  available: boolean;
  sort: number;
}

export interface CafeOrderLine {
  item_id: string | null;
  name: string;
  unit_price: string;
  quantity: number;
  line_total: string;
}

export interface CafeOrderRow {
  id: string;
  number: number;
  seat_label: string | null;
  status: "placed" | "preparing" | "delivered" | "cancelled";
  payment_status: "unpaid" | "paid";
  payment_method: string;
  total: string;
  note: string;
  via: string;
  created_by: string;
  created_at: string;
  paid_at: string | null;
  lines: CafeOrderLine[];
}

export const menuApi = {
  menu: (all = false, signal?: AbortSignal) => api<{ categories: MenuCategory[]; items: MenuItem[] }>("/v1/cafe/menu", { query: { all: all ? "true" : undefined }, signal }),
  createCategory: (name: string, sort: number) => api<MenuCategory>("/v1/cafe/menu/categories", { method: "POST", body: { name, sort } }),
  patchCategory: (id: string, body: Partial<Pick<MenuCategory, "name" | "sort" | "active">>) =>
    api<MenuCategory>(`/v1/cafe/menu/categories/${id}`, { method: "PATCH", body }),
  createItem: (body: { category_id: string; name: string; aliases: string[]; price: string; list_price: string | null; is_new: boolean }) =>
    api<MenuItem>("/v1/cafe/menu/items", { method: "POST", body }),
  patchItem: (id: string, body: { available?: boolean; price?: string; list_price?: string; clear_list_price?: boolean; is_new?: boolean }) =>
    api<MenuItem>(`/v1/cafe/menu/items/${id}`, { method: "PATCH", body }),
  placeOrder: (seatId: string, lines: { item_id: string; quantity: number }[], note: string, idempotencyKey: string) =>
    api<CafeOrderRow>(`/v1/cafe/seats/${seatId}/orders`, {
      method: "POST",
      body: { lines, note },
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  orders: (status: "open" | "all", seatId?: string, signal?: AbortSignal) =>
    api<{ items: CafeOrderRow[] }>("/v1/cafe/orders", { query: { status, seat_id: seatId }, signal }),
  setStatus: (id: string, status: "preparing" | "delivered" | "cancelled") =>
    api<CafeOrderRow>(`/v1/cafe/orders/${id}/status`, { method: "POST", body: { status } }),
  markPaid: (id: string) => api<CafeOrderRow>(`/v1/cafe/orders/${id}/paid`, { method: "POST" }),
};

/** "1234.50" -> "1,234.50", "45.00" -> "45". Works on the exact string; never through a float. */
export function formatBaht(amount: string): string {
  const [whole, frac = ""] = amount.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = whole.replace("-", "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${digits}${frac && frac !== "00" ? `.${frac.padEnd(2, "0").slice(0, 2)}` : ""}`;
}

/** Display-only sum of exact decimal strings, in satang integers (the server's total is what counts). */
export function sumBaht(lines: { price: string; quantity: number }[]): string {
  const satang = lines.reduce((n, l) => {
    const [w, f = "0"] = l.price.split(".");
    return n + (Number(w) * 100 + Number(f.padEnd(2, "0").slice(0, 2))) * l.quantity;
  }, 0);
  return `${Math.floor(satang / 100)}.${String(satang % 100).padStart(2, "0")}`;
}
