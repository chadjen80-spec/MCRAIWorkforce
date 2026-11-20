// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import type { Artifact } from "@/lib/types";
import { CaptionTools, captionVersions } from "./CaptionTools";

const th = dictionaries.th;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const art = (meta: Record<string, unknown> = {}) =>
  ({ id: "a1", kind: "caption", filename: "caption-01.txt", text_preview: "แคปชันเดิม", metadata: meta }) as unknown as Artifact;
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);
const body = (fetchMock: ReturnType<typeof vi.fn>, i = 0) => JSON.parse(String((fetchMock.mock.calls[i] as unknown as [string, RequestInit])[1].body));

describe("CaptionTools", () => {
  it("offers the five rewrite actions", () => {
    wrap(<CaptionTools artifact={art()} onUpdated={vi.fn()} />);
    for (const key of ["cap.regenerate", "cap.rewrite", "cap.tone", "cap.shorten", "cap.expand"] as const) {
      expect(screen.getByRole("button", { name: th[key] })).toBeTruthy();
    }
  });

  it("asks for a full rewrite and passes the new version up only when it succeeds", async () => {
    const updated = art({ versions: [{ text: "แคปชันเดิม", at: "2026-11-20T10:00:00Z", by: "agent:content", source: "original" }] });
    const fetchMock = vi.fn(async () => json({ artifact: updated, warnings: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const onUpdated = vi.fn();
    wrap(<CaptionTools artifact={art()} onUpdated={onUpdated} />);
    fireEvent.click(screen.getByRole("button", { name: th["cap.shorten"] }));
    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(updated));
    expect(body(fetchMock)).toEqual({ action: "shorten", instruction: null, tone: null });
    expect(String((fetchMock.mock.calls[0] as unknown as [string])[0])).toContain("/v1/artifacts/a1/regenerate-caption");
  });

  it("keeps the current caption when the AI or the checks fail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: { code: "CAPTION_QUALITY_FAILED", message: "'100%' is not in the verified facts" } }, 422)));
    const onUpdated = vi.fn();
    wrap(<CaptionTools artifact={art()} onUpdated={onUpdated} />);
    fireEvent.click(screen.getByRole("button", { name: th["cap.rewrite"] }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("100%");
    expect(onUpdated).not.toHaveBeenCalled();
  });

  it("sends the chosen tone and the owner's feedback", async () => {
    const fetchMock = vi.fn(async () => json({ artifact: art(), warnings: ["opens like caption 2"] }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<CaptionTools artifact={art()} onUpdated={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: th["cap.tone"] }));
    fireEvent.change(screen.getByLabelText(th["cap.toneLabel"]), { target: { value: "playful" } });
    fireEvent.change(screen.getByLabelText(th["cap.feedbackLabel"]), { target: { value: "พูดกับร้านกาแฟ" } });
    fireEvent.click(screen.getByRole("button", { name: th["cap.toneStart"] }));
    await screen.findByText("opens like caption 2");
    expect(body(fetchMock)).toEqual({ action: "tone", instruction: "พูดกับร้านกาแฟ", tone: "playful" });
  });

  it("lists earlier versions and restores one", async () => {
    const versions = [
      { text: "เวอร์ชันแรก", at: "2026-11-20T10:00:00Z", by: "agent:content", source: "original" },
      { text: "เวอร์ชันสอง", at: "2026-11-20T11:00:00Z", by: "user:a", source: "regenerate" },
    ];
    const fetchMock = vi.fn(async () => json(art()));
    vi.stubGlobal("fetch", fetchMock);
    const onUpdated = vi.fn();
    wrap(<CaptionTools artifact={art({ versions })} onUpdated={onUpdated} />);
    expect(captionVersions(art({ versions: [...versions, { bad: true }] }))).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: th["cap.history"].replace("{n}", "2") }));
    const list = screen.getByRole("list", { name: th["cap.historyTitle"] });
    expect(list.textContent).toContain(th["cap.source.regenerate"]);
    expect(list.textContent?.indexOf("เวอร์ชันสอง")).toBeLessThan(list.textContent?.indexOf("เวอร์ชันแรก") ?? 0); // newest first
    fireEvent.click(screen.getAllByRole("button", { name: th["cap.restore"] }).at(-1) as HTMLElement);
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
    expect(body(fetchMock)).toEqual({ text: "เวอร์ชันแรก" });
  });
});
