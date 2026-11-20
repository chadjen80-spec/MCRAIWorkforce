// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { askQtc, confirmQtc, qtcDecision, type QtcAnswer } from "@/lib/qtcAssistant";
import { QtcResultCard } from "./QtcResultCard";

const th = dictionaries.th;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);

const proposal: QtcAnswer = {
  intent: "create_quotation",
  agent: "quotation",
  reply: "เตรียมใบเสนอราคาฉบับร่างให้ลูกค้า ABC 2 รายการ",
  data: {
    lines: [
      { description: "งานโปรดักชั่นวิดีโอ", approved_price: "45000.0000" },
      { description: "ถ่ายโดรน", approved_price: null },
    ],
  },
  confirm: { action: "create_quotation", payload: { customer_id: "c1", brief: "x", lines: [] } },
};

describe("askQtc: sales and finance commands", () => {
  it("returns the structured answer for a recognised command", async () => {
    const fetchMock = vi.fn(async () => json(proposal));
    vi.stubGlobal("fetch", fetchMock);
    expect(await askQtc("สร้างใบเสนอราคางาน Production ให้ลูกค้า ABC", "th")).toEqual(proposal);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/v1/qtc/assistant");
    expect(JSON.parse(String(init.body))).toEqual({ text: "สร้างใบเสนอราคางาน Production ให้ลูกค้า ABC", language: "th" });
  });

  it("leaves everything else to Mira's usual flow, including when the server fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ intent: null, agent: null, reply: "", data: {}, confirm: null })));
    expect(await askQtc("ทำโปสเตอร์โปรโมชันร้านกาแฟ", "th")).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: { code: "INTERNAL", message: "boom" } }, 500)));
    expect(await askQtc("ลูกค้ารายไหนยังไม่จ่ายเงิน", "th")).toBeNull();
  });

  it("confirms with an idempotency key so a double confirm creates one record", async () => {
    const fetchMock = vi.fn(async () => json({ ...proposal, confirm: null }));
    vi.stubGlobal("fetch", fetchMock);
    await confirmQtc(proposal.confirm!, "th", "key-1");
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toMatchObject({ action: "create_quotation", idempotency_key: "key-1", language: "th" });
  });

  it("runs a proposal only on an explicit spoken yes", () => {
    expect(qtcDecision("ยืนยันค่ะ", "th")).toBe("yes");
    expect(qtcDecision("ไม่ใช่", "th")).toBe("no");
    expect(qtcDecision("ออกแบบ", "th")).toBe("unclear"); // "design" means nothing here
    expect(qtcDecision("hmm", "en")).toBe("unclear");
    expect(qtcDecision("no, cancel", "en")).toBe("no");
  });
});

describe("QtcResultCard", () => {
  it("marks lines without an approved price and offers Confirm / Cancel", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    wrap(<QtcResultCard answer={proposal} waiting onConfirm={onConfirm} onCancel={onCancel} />);
    expect(screen.getByText(th["qtcMira.confirmLabel"])).toBeTruthy();
    expect(screen.getByText("45,000.00")).toBeTruthy();
    expect(screen.getByText(th["qtcMira.needsPrice"])).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: th["qtcMira.confirm"] }));
    fireEvent.click(screen.getByRole("button", { name: th["qtcMira.cancel"] }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("shows receivable figures and a link, with no buttons for a plain answer", () => {
    const answer: QtcAnswer = {
      intent: "receivables_summary",
      agent: "finance",
      reply: "สรุปเดือนนี้",
      data: {
        link: "/qtc/invoices?status=open",
        figures: { outstanding: "47149.90", overdue: "47149.90", invoiced_this_month: "48150.00", received_this_month: "1000.10" },
      },
      confirm: null,
    };
    wrap(<QtcResultCard answer={answer} waiting={false} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getAllByText("47,149.90")).toHaveLength(2);
    expect(screen.getByText("1,000.10")).toBeTruthy();
    expect(screen.queryByRole("button", { name: th["qtcMira.confirm"] })).toBeNull();
    expect(screen.getByRole("link", { name: th["qtcMira.open"] }).getAttribute("href")).toBe("/qtc/invoices?status=open");
  });

  it("says reminder drafts are never sent", () => {
    const answer: QtcAnswer = {
      intent: "collection_drafts",
      agent: "collection",
      reply: "มีใบแจ้งหนี้เกินกำหนด 1 ใบ",
      data: { items: [{ invoice_id: "i1", number: "INV-2026-0001", customer: "ABC", balance: "4926.66" }] },
      confirm: { action: "draft_reminders", payload: { invoice_ids: ["i1"] } },
    };
    wrap(<QtcResultCard answer={answer} waiting onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText("INV-2026-0001 · ABC")).toBeTruthy();
    expect(screen.getByText("4,926.66")).toBeTruthy();
    expect(screen.getByText(th["qtcMira.noSend"])).toBeTruthy();
  });
});
