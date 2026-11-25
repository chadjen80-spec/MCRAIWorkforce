// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { askCafe, confirmCafe } from "@/lib/cafeAssistant";
import { formatBaht, formatRemaining, sumBaht, type CafeSeat } from "@/lib/cafe";
import { CafeMiraPanel } from "./CafeMiraPanel";
import { CafeShell } from "./CafeShell";
import { DevicesBoard, SeatCard, resultText } from "./DevicesBoard";
import { FoodOrder } from "./FoodOrder";
import { OrdersBoard } from "./OrdersBoard";

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
    expect(resultText(t, { status: "failed", result: { code: "NEEDS_ELEVATION" } })).toBe(th["cafe.result.NEEDS_ELEVATION"]);
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

describe("food & drink orders", () => {
  const menu = {
    categories: [
      { id: "c1", name: "อาหาร", sort: 1, active: true },
      { id: "c2", name: "เครื่องดื่ม", sort: 2, active: true },
    ],
    items: [
      { id: "i1", category_id: "c1", name: "มาม่าต้มยำ", description: "", aliases: [], price: "45.00", list_price: null, is_new: true, available: true, sort: 0 },
      { id: "i2", category_id: "c2", name: "โค้ก", description: "", aliases: [], price: "20.50", list_price: "25.00", is_new: false, available: true, sort: 0 },
      { id: "i3", category_id: "c1", name: "ขนมปังปิ้ง", description: "", aliases: [], price: "30.00", list_price: null, is_new: false, available: false, sort: 0 },
    ],
  };

  it("formats exact amounts without floating-point drift", () => {
    expect(formatBaht("1234.50")).toBe("1,234.50");
    expect(formatBaht("45.00")).toBe("45");
    expect(sumBaht([{ price: "0.10", quantity: 3 }, { price: "20.50", quantity: 2 }])).toBe("41.30");
  });

  it("builds a cart and sends only item IDs and quantities, then shows the server's order", async () => {
    const fetchMock = server({
      "GET /v1/cafe/menu": menu,
      "POST /v1/cafe/seats/s12/orders": {
        id: "o1", number: 7, seat_label: "12", status: "placed", payment_status: "unpaid", payment_method: "counter", total: "86.00",
        note: "ไม่เผ็ด", via: "seat", created_by: "user:a", created_at: "2026-11-25T10:00:00Z", paid_at: null, lines: [],
      },
    });
    const onOrdered = vi.fn();
    wrap(<FoodOrder seatId="s12" seatLabel="12" onOrdered={onOrdered} />);
    fireEvent.click(await screen.findByRole("button", { name: `มาม่าต้มยำ 45 ${th["food.baht"]}` }));
    fireEvent.click(screen.getByRole("button", { name: `โค้ก 20.50 ${th["food.baht"]}` }));
    fireEvent.click(screen.getByRole("button", { name: th["food.more"].replace("{item}", "โค้ก") }));
    expect((screen.getByRole("button", { name: `ขนมปังปิ้ง 30 ${th["food.baht"]}` }) as HTMLButtonElement).disabled).toBe(true); // sold out
    expect(screen.getByText(`86 ${th["food.baht"]}`)).toBeTruthy(); // 45 + 2 × 20.50, preview only
    fireEvent.change(screen.getByLabelText(th["food.note"]), { target: { value: "ไม่เผ็ด" } });
    fireEvent.click(screen.getByRole("button", { name: th["food.placeOrder"] }));
    expect(await screen.findByText(th["food.placed"].replace("{number}", "7").replace("{total}", "86"))).toBeTruthy();
    expect(onOrdered).toHaveBeenCalledWith(expect.objectContaining({ number: 7, total: "86.00" })); // Mira announces this
    const [, init] = fetchMock.mock.calls.find(([u]) => String(u).includes("/orders"))! as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ lines: [{ item_id: "i1", quantity: 1 }, { item_id: "i2", quantity: 2 }], note: "ไม่เผ็ด" });
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
  });

  it("lets staff move an order along and record counter payment", async () => {
    const order = {
      id: "o1", number: 3, seat_label: "12", status: "placed", payment_status: "unpaid", payment_method: "counter", total: "45.00", note: "",
      via: "mira", created_by: "user:a", created_at: "2026-11-25T10:00:00Z", paid_at: null,
      lines: [{ item_id: "i1", name: "มาม่าต้มยำ", unit_price: "45.00", quantity: 1, line_total: "45.00" }],
    };
    const fetchMock = server({
      "GET /v1/cafe/orders": { items: [order] },
      "POST /v1/cafe/orders/o1/status": { ...order, status: "preparing" },
      "POST /v1/cafe/orders/o1/paid": { ...order, payment_status: "paid" },
    });
    wrap(<OrdersBoard />);
    expect(await screen.findByText("#3")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: th["food.startPreparing"] }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/orders/o1/status"))).toBe(true));
    const statusCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/orders/o1/status"))! as unknown as [string, RequestInit];
    expect(JSON.parse(String(statusCall[1].body))).toEqual({ status: "preparing" });
    fireEvent.click(screen.getByRole("button", { name: th["food.markPaid"] }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/orders/o1/paid"))).toBe(true));
  });
});

describe("Mira food orders", () => {
  it("shows each proposed line with the server's price on her confirmation card", async () => {
    server({
      "POST /v1/cafe/assistant": {
        intent: "order_food",
        tool_call: { name: "order_food", arguments: {}, source: "rules" },
        reply: "สั่ง มาม่าต้มยำ x2 90 บาท รวม 90 บาท ส่งที่ที่นั่ง 12 ชำระที่เคาน์เตอร์ ใช่ไหมคะ",
        data: { total: "90.00", lines: [{ name: "มาม่าต้มยำ", quantity: 2, unit_price: "45.00", line_total: "90.00" }] },
        confirm: { action: "order_food", payload: { seat_id: "s12", expected_total: "90.00", lines: [{ item_id: "i1", quantity: 2 }] } },
      },
    });
    const a = await askCafe("สั่งมาม่า 2 ที่ เครื่อง 12", "th");
    expect(a?.data.lines).toEqual([{ description: "มาม่าต้มยำ ×2", approved_price: "90.00" }]);
    expect(a?.confirm?.action).toBe("order_food");
  });
});
