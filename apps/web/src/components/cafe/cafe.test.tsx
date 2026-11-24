// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { askCafe, confirmCafe } from "@/lib/cafeAssistant";
import { formatRemaining, type CafeSeat } from "@/lib/cafe";
import { CafeMiraPanel } from "./CafeMiraPanel";
import { CafeShell } from "./CafeShell";
import { DevicesBoard, SeatCard, resultText } from "./DevicesBoard";

vi.mock("next/navigation", () => ({ usePathname: () => "/cafe/devices", useRouter: () => ({ push: vi.fn() }) }));

const th = dictionaries.th;
const t = (k: keyof typeof th) => th[k];
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);

const seat: CafeSeat = {
  id: "s12",
  label: "12",
  zone: "A",
  status: "active",
  device: {
    id: "d1",
    name: "PC-12",
    online: true,
    client_version: "0.1.0",
    os_version: "Windows",
    last_heartbeat_at: "2026-11-24T10:00:00Z",
    installed_apps: { "demo-game": "installed", steam: "missing" },
  },
  session: { id: "cs1", started_at: "2026-11-24T09:00:00Z", granted_seconds: 3600, remaining_seconds: 3125 },
  open_assists: 1,
};
const features = (enabled: boolean, modules: Record<string, boolean> = {}) => ({
  enabled,
  modules: { devices: enabled, sessions: enabled, launcher: enabled, assistant: enabled, assist: enabled, orders: false, pos: false, members: false, printing: false, reports: false, ...modules },
});

/** Routes /api/proxy/v1/cafe/... calls to canned answers. */
function server(routes: Record<string, unknown | ((init: RequestInit) => unknown)>) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = url.replace("/api/proxy", "").split("?")[0];
    const key = `${init.method ?? "GET"} ${path}`;
    const hit = routes[key];
    if (hit === undefined) return json({ error: { code: "NOT_FOUND", message: key } }, 404);
    return json(typeof hit === "function" ? (hit as (i: RequestInit) => unknown)(init) : hit);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("feature flags", () => {
  it("shows that the café is switched off instead of any data", async () => {
    server({ "GET /v1/cafe/features": features(false) });
    wrap(<CafeShell module="devices">{() => <p>live data</p>}</CafeShell>);
    expect(await screen.findByText(th["cafe.disabled.title"])).toBeTruthy();
    expect(screen.queryByText("live data")).toBeNull();
  });

  it("an unfinished module says it is not enabled", async () => {
    server({ "GET /v1/cafe/features": features(true) });
    wrap(<CafeShell module="orders">{() => <p>live data</p>}</CafeShell>);
    expect(await screen.findByText(th["cafe.module.off.title"].replace("{module}", "orders"))).toBeTruthy();
    expect(screen.queryByText("live data")).toBeNull();
  });
});

describe("devices page", () => {
  it("shows each seat's PC status, installed apps, session and the real command results", async () => {
    server({
      "GET /v1/cafe/seats": { items: [seat, { ...seat, id: "s13", label: "13", device: { ...seat.device!, online: false }, session: null, open_assists: 0 }] },
      "GET /v1/cafe/apps": { items: [] },
      "GET /v1/cafe/assist": { items: [] },
      "GET /v1/cafe/commands": {
        items: [
          { id: "c1", type: "launch_app", args: { app_id: "demo-game" }, status: "succeeded", result: { code: "STARTED", pid: 9 }, seat_label: "12", requested_by: "user:a", via: "mira", created_at: "2026-11-24T10:00:00Z", completed_at: null },
          { id: "c2", type: "launch_app", args: { app_id: "steam" }, status: "failed", result: { code: "NOT_ALLOWLISTED" }, seat_label: "12", requested_by: "user:a", via: "ui", created_at: "2026-11-24T10:00:00Z", completed_at: null },
        ],
      },
    });
    wrap(<DevicesBoard />);
    expect(await screen.findByText(`${th["cafe.seat"]} 12`)).toBeTruthy();
    expect(screen.getByText(th["cafe.online"])).toBeTruthy();
    expect(screen.getByText(th["cafe.offline"])).toBeTruthy();
    expect(screen.getAllByText(`demo-game · ${th["cafe.appInstalled"]}`).length).toBe(2);
    expect(screen.getAllByText(`steam · ${th["cafe.appMissing"]}`).length).toBe(2);
    expect(screen.getByText(th["cafe.remaining"].replace("{time}", "0:52:05"))).toBeTruthy();
    expect(await screen.findByText(th["cafe.result.STARTED"])).toBeTruthy();
    expect(screen.getByText(th["cafe.result.NOT_ALLOWLISTED"])).toBeTruthy();
  });

  it("launch sends an idempotency key and then shows what the PC answered", async () => {
    let polls = 0;
    const fetchMock = server({
      "POST /v1/cafe/seats/s12/launch": { id: "c9", status: "queued", result: {}, args: { app_id: "demo-game" } },
      "GET /v1/cafe/commands/c9": () => (++polls < 2 ? { id: "c9", status: "delivered", result: {} } : { id: "c9", status: "succeeded", result: { code: "STARTED", pid: 4 } }),
    });
    const apps = [{ id: "a1", app_key: "demo-game", name: "Demo Game", aliases: [], category: "game", enabled: true, requires_session: true }];
    wrap(<SeatCard seat={seat} apps={apps} onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText(th["cafe.launchPick"]), { target: { value: "demo-game" } });
    fireEvent.click(screen.getByRole("button", { name: th["cafe.launch"] }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain(th["cafe.result.STARTED"]), { timeout: 4000 });
    const launch = fetchMock.mock.calls.find(([u]) => String(u).includes("/launch"))!;
    const init = launch[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({ app_key: "demo-game" });
  });

  it("never reports an unanswered command as a success", () => {
    expect(resultText(t, { status: "delivered", result: {} })).toBe(th["cafe.result.waiting"]);
    expect(resultText(t, { status: "failed", result: { code: "WEIRD" } })).toBe(`${th["cafe.result.START_FAILED"]} (WEIRD)`);
    expect(formatRemaining(3725)).toBe("1:02:05");
    expect(formatRemaining(-5)).toBe("0:00:00");
  });
});

describe("Mira café commands", () => {
  it("asks the café assistant and marks the answer as a café one", async () => {
    const fetchMock = server({
      "POST /v1/cafe/assistant": {
        intent: "launch_app",
        tool_call: { name: "launch_app", arguments: { app: "Steam", seat: "12" }, source: "rules" },
        reply: "เปิด Steam ที่ที่นั่ง 12 ใช่ไหมคะ",
        data: { seat_label: "12" },
        confirm: { action: "launch_app", payload: { seat_id: "s12", app_key: "steam" } },
      },
      "POST /v1/cafe/assistant/confirm": { intent: "launch_app", reply: "เปิด Steam ที่ที่นั่ง 12 แล้วค่ะ", data: { status: "succeeded" }, confirm: null },
    });
    const a = await askCafe("เปิด Steam ที่เครื่อง 12", "th");
    expect(a?.source).toBe("cafe");
    expect(a?.data.tool_call).toEqual({ name: "launch_app", arguments: { app: "Steam", seat: "12" }, source: "rules" });
    const done = await confirmCafe(a!.confirm!, "th", "key-1");
    expect(done.reply).toBe("เปิด Steam ที่ที่นั่ง 12 แล้วค่ะ");
    const body = JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body));
    expect(body).toEqual({ action: "launch_app", payload: { seat_id: "s12", app_key: "steam" }, language: "th", idempotency_key: "key-1" });
  });

  it("falls back to Mira's other flows when the café is off or unreachable", async () => {
    server({ "POST /v1/cafe/assistant": { intent: null, reply: "", data: {}, confirm: null } });
    expect(await askCafe("ทำโปสเตอร์โปรโมชัน", "th")).toBeNull();
    server({}); // 404 FEATURE_DISABLED
    expect(await askCafe("เปิด Steam", "th")).toBeNull();
  });
});

describe("café view inside Mira's screen", () => {
  it("shows live figures and every seat with its PC status and time left", async () => {
    server({
      "GET /v1/cafe/features": features(true),
      "GET /v1/cafe/seats": { items: [seat, { ...seat, id: "s13", label: "13", device: null, session: null, open_assists: 0 }] },
      "GET /v1/cafe/assist": { items: [{ id: "h1", seat_label: "12", message: "", source: "seat", status: "open", created_at: "2026-11-24T10:00:00Z" }] },
    });
    wrap(<CafeMiraPanel />);
    expect(await screen.findByText(`${th["cafe.seat"]} 12`)).toBeTruthy();
    expect(screen.getByText(`${th["cafe.seat"]} 13`)).toBeTruthy();
    expect(screen.getByText(th["cafe.online"])).toBeTruthy();
    expect(screen.getByText(th["cafe.noDevice"])).toBeTruthy();
    expect(screen.getByText(th["cafe.remaining"].replace("{time}", "0:52:05"))).toBeTruthy();
    const figure = (key: keyof typeof th) => screen.getByText(th[key]).nextElementSibling?.textContent;
    expect([figure("cafe.overview.seats"), figure("cafe.overview.online"), figure("cafe.overview.active"), figure("cafe.overview.help")]).toEqual(["2", "1", "1", "1"]);
  });

  it("says the café is switched off instead of showing anything", async () => {
    server({ "GET /v1/cafe/features": features(false) });
    wrap(<CafeMiraPanel />);
    expect(await screen.findByText(th["cafe.disabled.title"])).toBeTruthy();
  });
});
