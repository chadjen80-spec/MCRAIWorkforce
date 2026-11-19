// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { ApprovalPanel } from "./ApprovalPanel";

const th = dictionaries.th;

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function setup(response: Response) {
  const fetchMock = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetchMock);
  const onDone = vi.fn();
  render(
    <I18nProvider>
      <ApprovalPanel jobId="job-1" onDone={onDone} />
    </I18nProvider>,
  );
  return { fetchMock, onDone };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ApprovalPanel", () => {
  it("approves with the reviewer's comment and refreshes the job", async () => {
    const { fetchMock, onDone } = setup(reply(200, { id: "job-1", status: "completed" }));
    fireEvent.change(screen.getByLabelText(th["monitor.comment"]), { target: { value: "  looks good  " } });
    fireEvent.click(screen.getByRole("button", { name: th["monitor.approve"] }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/proxy/v1/jobs/job-1/approve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ decision: "approved", comment: "looks good" });
  });

  it("rejects without a comment when none was typed", async () => {
    const { fetchMock, onDone } = setup(reply(200, { id: "job-1", status: "rejected" }));
    fireEvent.click(screen.getByRole("button", { name: th["monitor.reject"] }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toEqual({ decision: "rejected" });
  });

  it("shows the server's refusal to a member who may not approve", async () => {
    const { onDone } = setup(reply(403, { error: { code: "FORBIDDEN", message: "Owner or admin role required" } }));
    fireEvent.click(screen.getByRole("button", { name: th["monitor.approve"] }));
    expect(await screen.findByText(/Owner or admin role required/)).toBeTruthy();
    expect(onDone).toHaveBeenCalled(); // the page reloads and keeps showing the job as awaiting approval
  });

  it("disables both buttons while a decision is being sent (no double approval)", async () => {
    let finish: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => (finish = r))));
    render(
      <I18nProvider>
        <ApprovalPanel jobId="job-1" onDone={() => {}} />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: th["monitor.approve"] }));
    await waitFor(() => {
      for (const b of screen.getAllByRole("button")) expect((b as HTMLButtonElement).disabled).toBe(true);
    });
    finish(reply(200, { id: "job-1", status: "completed" }));
  });
});
