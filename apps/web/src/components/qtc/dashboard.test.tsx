// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { ApiError } from "@/lib/api";
import { QtcDashboard, type QtcSummary } from "./QtcDashboard";
import { QtcError } from "./QtcShell";

const th = dictionaries.th;
afterEach(cleanup);
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);
const fig = (count: number, amount: string) => ({ count, amount });
const base: QtcSummary = {
  as_of: "2026-11-20",
  month: "2026-11",
  pipeline: fig(0, "0.00"),
  awaiting_approval: fig(0, "0.00"),
  accepted_this_month: fig(0, "0.00"),
  outstanding: fig(0, "0.00"),
  overdue: fig(0, "0.00"),
  cash_received: fig(0, "0.00"),
  counts: { open_quotations: 0, open_invoices: 0 },
};

describe("QtcDashboard", () => {
  it("shows the six figures exactly, with links, and highlights what needs attention", () => {
    wrap(
      <QtcDashboard
        summary={{
          ...base,
          pipeline: fig(1, "8560.00"),
          awaiting_approval: fig(1, "8560.00"),
          accepted_this_month: fig(1, "48150.00"),
          outstanding: fig(1, "28890.00"),
          overdue: fig(1, "28890.00"),
          cash_received: fig(1, "19260.00"),
        }}
      />,
    );
    const tile = (label: string) => screen.getByText(label).closest("a") as HTMLAnchorElement;
    expect(tile(th["qtc.dash.pipeline"]).textContent).toContain("8,560.00");
    expect(tile(th["qtc.dash.accepted"]).textContent).toContain("48,150.00");
    expect(tile(th["qtc.dash.cash"]).textContent).toContain("19,260.00");
    expect(tile(th["qtc.dash.overdue"]).getAttribute("href")).toBe("/qtc/invoices?status=overdue");
    expect(tile(th["qtc.dash.overdue"]).className).toContain("border-warn");
    expect(tile(th["qtc.dash.outstanding"]).className).not.toContain("border-warn");
    expect(tile(th["qtc.dash.awaiting"]).textContent).toContain(th["qtc.dash.quotes"].replace("{n}", "1"));
  });

  it("explains what to do when there is nothing yet", () => {
    wrap(<QtcDashboard summary={base} />);
    expect(screen.getByText(th["qtc.dash.emptyTitle"])).toBeTruthy();
    expect(screen.queryByText(th["qtc.dash.pipeline"])).toBeNull();
  });

  it("shows a permission message instead of figures when access is denied", () => {
    wrap(<QtcError error={new ApiError(403, "FORBIDDEN", "This API key does not have the 'qtc:read' scope")} />);
    expect(screen.getByRole("alert").textContent).toContain(th["qtc.denied"]);
  });
});
