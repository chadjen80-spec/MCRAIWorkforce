// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import type { DashboardSummary, Job } from "@/lib/types";
import { CommandComposer } from "./CommandComposer";
import { OpsSummary, TaskSuggestions } from "./OpsPanel";
import { QuickActions } from "./QuickActions";

const th = dictionaries.th;
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);
afterEach(cleanup);

function Composer({ onSubmit, initial = "" }: { onSubmit: (v: string) => void; initial?: string }) {
  const [value, setValue] = useState(initial);
  return <CommandComposer value={value} onChange={setValue} onSubmit={() => onSubmit(value)} minLength={5} label="Command" />;
}

describe("CommandComposer", () => {
  const box = () => screen.getByLabelText("Command") as HTMLTextAreaElement;

  it("is a multiline box: Shift+Enter keeps typing, Enter sends", () => {
    const onSubmit = vi.fn();
    wrap(<Composer onSubmit={onSubmit} />);
    expect(box().tagName).toBe("TEXTAREA");
    fireEvent.change(box(), { target: { value: "3 images\nand 5 captions" } });
    fireEvent.keyDown(box(), { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("3 images\nand 5 captions");
  });

  it("does not send while a Thai input method is still composing", () => {
    const onSubmit = vi.fn();
    wrap(<Composer onSubmit={onSubmit} initial="ภาพโปรโมต 3 ภาพ" />);
    fireEvent.keyDown(box(), { key: "Enter", isComposing: true });
    fireEvent.keyDown(box(), { key: "Enter", keyCode: 229 });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("keeps Send disabled until the command is long enough", () => {
    const onSubmit = vi.fn();
    wrap(<Composer onSubmit={onSubmit} initial="hi" />);
    const send = screen.getByRole("button", { name: th["assistant.send"] }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.change(box(), { target: { value: "5 captions" } });
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});

describe("QuickActions", () => {
  it("hands the picked workflow to the page (which fills the composer; nothing is submitted)", () => {
    const onPick = vi.fn();
    wrap(<QuickActions onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(th["qa.quotation.title"]) }));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: "quotation", kind: "command" }));
  });

  it("shows planned Quote-to-Cash steps as labels, never as buttons", () => {
    wrap(<QuickActions onPick={vi.fn()} />);
    for (const key of ["planned.invoice", "planned.payment", "planned.followup"] as const) {
      const item = screen.getByText(th[key]);
      expect(item.closest("button")).toBeNull();
      expect(item.closest("a")).toBeNull();
      expect(within(item.closest("li")!).getByText(th["planned.badge"])).toBeTruthy();
    }
  });

  it("warns when a workflow's worker is offline and disables every card while designing", () => {
    const { rerender } = wrap(<QuickActions onPick={vi.fn()} offline={["video"]} />);
    expect(screen.getByText(th["qa.offline"].replace("{queues}", "video"))).toBeTruthy();
    rerender(
      <I18nProvider>
        <QuickActions onPick={vi.fn()} disabled disabledReason="close the design first" />
      </I18nProvider>,
    );
    expect(screen.getByText("close the design first")).toBeTruthy();
    for (const b of screen.getAllByRole("button")) expect((b as HTMLButtonElement).disabled).toBe(true);
  });
});

const summary: DashboardSummary = {
  active_jobs: 1,
  completed_jobs: 4,
  failed_jobs: 1,
  awaiting_approval_jobs: 5,
  month_cost_usd: 12.5,
  active_estimated_cost_usd: 0,
  monthly_budget_usd: 50,
  recent_artifacts: [],
  offline_queues: [],
};
const job = (id: string, command: string) => ({ id, command, created_at: "2026-11-20T02:00:00Z", estimated_cost_usd: 0.5, actual_cost_usd: null }) as unknown as Job;

describe("OpsSummary", () => {
  it("links each waiting job straight to its review page, and the count to the filtered list", () => {
    wrap(<OpsSummary summary={summary} waiting={[job("j1", "3 promo images"), job("j2", "quotation for ACME")]} waitingTotal={5} />);
    const reviews = screen.getAllByRole("link", { name: new RegExp(`^${th["ops.review"]}:`) });
    expect(reviews.map((a) => a.getAttribute("href"))).toEqual(["/jobs/j1", "/jobs/j2"]);
    expect(screen.getByRole("link", { name: new RegExp(th["ops.awaiting"]) }).getAttribute("href")).toBe("/jobs?status=awaiting_approval");
    expect(screen.getByRole("link", { name: th["ops.viewAll"].replace("{n}", "5") }).getAttribute("href")).toBe("/jobs?status=awaiting_approval");
  });

  it("says so when nothing waits, and warns about offline workers", () => {
    wrap(<OpsSummary summary={{ ...summary, awaiting_approval_jobs: 0, offline_queues: ["image"] }} waiting={[]} waitingTotal={0} />);
    expect(screen.getByText(th["ops.noneWaiting"])).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("image");
  });
});

describe("TaskSuggestions", () => {
  it("opens pages for things waiting on you and starts quick actions for next jobs", () => {
    const onStart = vi.fn();
    wrap(<TaskSuggestions summary={{ ...summary, failed_jobs: 0, month_cost_usd: 1 }} onStart={onStart} />);
    expect(screen.getByRole("link", { name: /5/ }).getAttribute("href")).toBe("/jobs?status=awaiting_approval");
    fireEvent.click(screen.getByRole("button", { name: th["suggest.quotation"] }));
    expect(onStart).toHaveBeenCalledWith("quotation");
  });
});
