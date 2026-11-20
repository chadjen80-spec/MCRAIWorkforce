// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { percentsTotal } from "@/lib/qtc";
import { CreateOrder, InvoiceStatusBadge, PaymentStatusBadge } from "./BillingParts";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/qtc/quotations/q1" }));

const th = dictionaries.th;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);

describe("percentsTotal: schedule percentages on the digits", () => {
  it("adds exactly, without floating point", () => {
    expect(percentsTotal(["33.33", "33.33", "33.34"])).toBe("100.00");
    expect(percentsTotal(["30", "70"])).toBe("100.00");
    expect(percentsTotal(["0.1", "0.2"])).toBe("0.30"); // 0.1 + 0.2 in floats is 0.30000000000000004
    expect(percentsTotal(["30", "abc"])).toBeNull();
    expect(percentsTotal(["1.234"])).toBeNull();
  });
});

describe("CreateOrder", () => {
  it("only allows a schedule that adds up to exactly 100% and sends it", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: "o1" }), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<CreateOrder quotationId="q1" />);
    const create = () => screen.getAllByRole("button", { name: th["qtc.o.create"] }).at(-1) as HTMLButtonElement;
    expect(screen.getByRole("status").textContent).toBe(th["qtc.o.sum"].replace("{pct}", "100"));
    const pcts = screen.getAllByLabelText(th["qtc.o.percent"]);
    fireEvent.change(pcts[0], { target: { value: "40" } });
    expect(screen.getByRole("status").textContent).toBe(th["qtc.o.sum"].replace("{pct}", "110"));
    expect(create().disabled).toBe(true);
    fireEvent.change(pcts[1], { target: { value: "60" } });
    expect(create().disabled).toBe(false);
    fireEvent.click(create());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/qtc/orders/o1"));
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toEqual({
      quotation_id: "q1",
      schedule: [
        { kind: "deposit", description: th["qtc.o.kind.deposit"], percent: "40" },
        { kind: "final", description: th["qtc.o.kind.final"], percent: "60" },
      ],
    });
  });

  it("adds installments before the final payment", () => {
    wrap(<CreateOrder quotationId="q1" />);
    fireEvent.click(screen.getByRole("button", { name: th["qtc.o.addPart"] }));
    const labels = screen.getAllByLabelText(/\d\. /).map((el) => el.closest("div")?.querySelector("label")?.textContent);
    expect(labels).toEqual([`1. ${th["qtc.o.kind.deposit"]}`, `2. ${th["qtc.o.kind.installment"]}`, `3. ${th["qtc.o.kind.final"]}`]);
  });
});

describe("status badges", () => {
  it("label overdue invoices and payments waiting for confirmation", () => {
    wrap(
      <>
        <InvoiceStatusBadge status="overdue" />
        <PaymentStatusBadge status="pending_confirmation" />
      </>,
    );
    expect(screen.getByText(th["qtc.i.status.overdue"])).toBeTruthy();
    expect(screen.getByText(th["qtc.p.status.pending_confirmation"])).toBeTruthy();
  });
});
