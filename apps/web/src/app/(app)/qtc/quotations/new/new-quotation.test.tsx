// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import NewQuotationPage from "./page";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/qtc/quotations/new" }));

const th = dictionaries.th;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  push.mockClear();
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const page = (items: unknown[]) => ({ items, total: items.length, limit: 100, offset: 0 });

function backend(submitStatus = 200) {
  const calls: { url: string; method: string; body: unknown; key: string | null }[] = [];
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const headers = new Headers(init?.headers);
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null, key: headers.get("Idempotency-Key") });
    if (url.includes("/v1/qtc/customers")) return json(page([{ id: "c1", code: "C0001", name: "ABC" }]));
    if (url.includes("/v1/qtc/services")) return json(page([{ id: "s1", code: "PROD", name: "งานโปรดักชั่น", unit: "งาน", default_price: "45000.0000" }]));
    if (url.includes("/v1/qtc/price-lists")) return json(page([{ id: "pl", is_default: true }]));
    if (url.endsWith("/submit")) return submitStatus === 200 ? json({ id: "q1", status: "pending_approval" }) : json({ error: { code: "INVALID_STATE", message: "nope" } }, submitStatus);
    if (url.includes("/v1/qtc/quotations") && method === "POST") return json({ id: "q1", status: "draft" }, 201);
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const wrap = () => render(<I18nProvider><NewQuotationPage /></I18nProvider>);
const submitBtn = () => screen.getByRole("button", { name: th["qtc.q.submit"] }) as HTMLButtonElement;

describe("New quotation: Submit for approval", () => {
  it("is enabled once every line has a price, and creates then submits the quotation", async () => {
    const calls = backend();
    wrap();
    const customer = await screen.findByLabelText(th["qtc.q.customer"]);
    expect(submitBtn().disabled).toBe(true);
    fireEvent.change(customer, { target: { value: "c1" } });
    fireEvent.click(screen.getByRole("button", { name: th["qtc.q.addCustom"] }));
    fireEvent.change(screen.getByLabelText(th["qtc.q.description"]), { target: { value: "งานกราฟิก" } });
    expect(submitBtn().disabled).toBe(true); // no price yet
    expect(screen.getByText(th["qtc.q.submitNeedsPrices"])).toBeTruthy();
    fireEvent.change(screen.getByLabelText(th["qtc.q.price"]), { target: { value: "500" } });
    expect(submitBtn().disabled).toBe(false);
    fireEvent.click(submitBtn());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/qtc/quotations/q1"));
    const writes = calls.filter((c) => c.method === "POST");
    expect(writes.map((c) => c.url.replace(/^.*\/v1/, "/v1"))).toEqual(["/v1/qtc/quotations", "/v1/qtc/quotations/q1/submit"]);
    expect(writes[0].key).toBeTruthy();
  });

  it("keeps the form and the same draft when submitting fails", async () => {
    const calls = backend(409);
    wrap();
    fireEvent.change(await screen.findByLabelText(th["qtc.q.customer"]), { target: { value: "c1" } });
    fireEvent.click(screen.getByRole("button", { name: th["qtc.q.addCustom"] }));
    fireEvent.change(screen.getByLabelText(th["qtc.q.description"]), { target: { value: "งานกราฟิก" } });
    fireEvent.change(screen.getByLabelText(th["qtc.q.price"]), { target: { value: "500" } });
    fireEvent.click(submitBtn());
    await screen.findByText("nope");
    expect(push).not.toHaveBeenCalled();
    fireEvent.click(submitBtn());
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/submit"))).toHaveLength(2));
    const creates = calls.filter((c) => c.method === "POST" && !c.url.endsWith("/submit"));
    expect(new Set(creates.map((c) => c.key)).size).toBe(1); // the retry reuses the same draft
  });
});
