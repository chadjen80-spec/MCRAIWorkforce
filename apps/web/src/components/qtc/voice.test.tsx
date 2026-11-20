// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { QtcVoice } from "./QtcVoice";

const { push, heard } = vi.hoisted(() => ({ push: vi.fn(), heard: { text: "" } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/qtc" }));
vi.mock("@/lib/voice", async (orig) => ({
  ...(await orig<typeof import("@/lib/voice")>()),
  canListen: () => true,
  listenOnce: vi.fn(async () => heard.text),
  speak: vi.fn(async () => undefined),
}));

const th = dictionaries.th;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
beforeEach(() => push.mockClear());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const wrap = () => render(<I18nProvider><QtcVoice /></I18nProvider>);
const speakButton = () => screen.getByRole("button", { name: new RegExp(th["qtcVoice.button"]) });

describe("Voice control on Sales & Finance pages", () => {
  it("opens the page that was named", async () => {
    heard.text = "เพิ่มลูกค้า";
    vi.stubGlobal("fetch", vi.fn());
    wrap();
    fireEvent.click(speakButton());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/qtc/customers?new=1"));
    expect(fetch).not.toHaveBeenCalled(); // page names are recognised locally
  });

  it("answers finance questions from the server", async () => {
    heard.text = "ลูกค้ารายไหนยังไม่จ่ายเงิน";
    vi.stubGlobal("fetch", vi.fn(async () => json({ intent: "unpaid_customers", agent: "finance", reply: "มีลูกค้าค้างชำระ 1 ราย", data: { items: [] }, confirm: null })));
    wrap();
    fireEvent.click(speakButton());
    expect(await screen.findByText("มีลูกค้าค้างชำระ 1 ราย")).toBeTruthy();
    expect(push).not.toHaveBeenCalled();
  });

  it("runs a proposal only after Confirm", async () => {
    heard.text = "เตรียมข้อความติดตามเงินสำหรับ Invoice ที่เกินกำหนด";
    const fetchMock = vi.fn(async (input: string | URL | Request) =>
      String(input).endsWith("/confirm")
        ? json({ intent: "collection_drafts", agent: "collection", reply: "เตรียมร่างแล้ว 1 ฉบับ", data: {}, confirm: null })
        : json({ intent: "collection_drafts", agent: "collection", reply: "มีใบแจ้งหนี้เกินกำหนด 1 ใบ", data: { items: [] }, confirm: { action: "draft_reminders", payload: { invoice_ids: ["i1"] } } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    wrap();
    fireEvent.click(speakButton());
    await screen.findByText("มีใบแจ้งหนี้เกินกำหนด 1 ใบ");
    expect(fetchMock).toHaveBeenCalledTimes(1); // nothing written yet
    fireEvent.click(screen.getByRole("button", { name: th["qtcMira.confirm"] }));
    expect(await screen.findByText("เตรียมร่างแล้ว 1 ฉบับ")).toBeTruthy();
    expect(String((fetchMock.mock.calls[1] as unknown as [string])[0])).toContain("/v1/qtc/assistant/confirm");
  });

  it("says when it is not a Sales & Finance command", async () => {
    heard.text = "ทำโปสเตอร์ร้านกาแฟ";
    vi.stubGlobal("fetch", vi.fn(async () => json({ intent: null, agent: null, reply: "", data: {}, confirm: null })));
    wrap();
    fireEvent.click(speakButton());
    expect(await screen.findByText(th["qtcVoice.notUnderstood"])).toBeTruthy();
  });
});
