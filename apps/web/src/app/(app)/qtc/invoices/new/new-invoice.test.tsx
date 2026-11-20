// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import NewInvoicePage from "./page";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => "/qtc/invoices/new",
  useSearchParams: () => new URLSearchParams(),
}));

const th = dictionaries.th;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  push.mockClear();
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const ms = (id: string, kind: string, status: string, total: string, position: number) => ({
  id, kind, status, position, description: `งวด ${position}`, due_date: null, amount_net: "0", amount_vat: "0", amount_total: total, invoice_id: null,
});
const order = {
  id: "o1", number: "SO-2026-0001", status: "open", customer: { id: "c1", code: "C0001", name: "ABC" }, grand_total: "48150.00",
  milestones: [ms("m1", "deposit", "invoiced", "19260.00", 1), ms("m2", "final", "pending", "28890.00", 2), ms("m3", "delivery", "pending", "0", 3)],
};

function backend(orders: unknown[], issueStatus = 200) {
  const writes: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if ((init?.method ?? "GET") === "POST") writes.push(url.replace(/^.*\/v1/, "/v1"));
    if (url.includes("/v1/qtc/orders")) return json({ items: orders, total: orders.length, limit: 100, offset: 0 });
    if (url.endsWith("/issue")) return issueStatus === 200 ? json({ id: "i1", status: "issued" }) : json({ error: { code: "INVALID_STATE", message: "cannot issue" } }, issueStatus);
    if (url.includes("/v1/qtc/invoices")) return json({ id: "i1", status: "draft" }, 201);
    return json({});
  }));
  return writes;
}
const wrap = () => render(<I18nProvider><NewInvoicePage /></I18nProvider>);

describe("New invoice", () => {
  it("bills one milestone of an open order that is not invoiced yet, then issues it", async () => {
    const writes = backend([order]);
    wrap();
    fireEvent.change(await screen.findByLabelText(th["qtc.i.order"]), { target: { value: "o1" } });
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(1); // the invoiced deposit and the delivery step are not offered
    expect(screen.getByText("28,890.00")).toBeTruthy();
    const issue = screen.getByRole("button", { name: th["qtc.i.createIssue"] }) as HTMLButtonElement;
    expect(issue.disabled).toBe(true);
    fireEvent.click(radios[0]);
    fireEvent.click(issue);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/qtc/invoices/i1"));
    expect(writes).toEqual(["/v1/qtc/invoices", "/v1/qtc/invoices/i1/issue"]);
  });

  it("does not create a second invoice when issuing fails and is retried", async () => {
    const writes = backend([order], 409);
    wrap();
    fireEvent.change(await screen.findByLabelText(th["qtc.i.order"]), { target: { value: "o1" } });
    fireEvent.click(screen.getAllByRole("radio")[0]);
    fireEvent.click(screen.getByRole("button", { name: th["qtc.i.createIssue"] }));
    await screen.findByText("cannot issue");
    fireEvent.click(screen.getByRole("button", { name: th["qtc.i.createIssue"] }));
    await waitFor(() => expect(writes.filter((w) => w.endsWith("/issue"))).toHaveLength(2));
    expect(writes.filter((w) => w === "/v1/qtc/invoices")).toHaveLength(1);
    expect(push).not.toHaveBeenCalled();
  });

  it("explains where invoices come from when nothing can be billed", async () => {
    backend([{ ...order, milestones: [ms("m1", "deposit", "invoiced", "1", 1)] }]);
    wrap();
    expect(await screen.findByText(th["qtc.i.newNoOrders"])).toBeTruthy();
  });
});
