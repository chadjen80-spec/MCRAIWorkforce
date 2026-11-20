// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import type { Artifact, Job, QaReport, Task } from "@/lib/types";
import { AgentGraph } from "./AgentGraph";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const th = dictionaries.th;
afterEach(cleanup);

const job = (over: Partial<Job> = {}) =>
  ({ id: "j1", status: "awaiting_approval", progress: 100, requested_agents: [], require_approval: false, approval_status: "pending",
     qa_verdict: "HUMAN_REVIEW", qa_summary: "1 item needs review", actual_cost_usd: 0.07, started_at: "2026-11-20T10:00:00Z",
     finished_at: "2026-11-20T10:02:00Z", error_code: null, error_message: null, ...over }) as Job;
const task = (key: string, agent: Task["agent"], over: Partial<Task> = {}) =>
  ({ id: `id-${key}`, key, agent, title: key, status: "succeeded", attempt: 1, max_attempts: 3, depends_on: [], provider: null,
     execution_location: null, routing_reason: null, error_code: null, error_message: null, estimated_cost_usd: null,
     actual_cost_usd: null, queued_at: null, started_at: null, finished_at: null, ...over }) as Task;

const tasks = [
  task("plan", "planner"),
  task("img_1", "image", { provider: "comfyui", execution_location: "local", started_at: "2026-11-20T10:00:00Z", finished_at: "2026-11-20T10:01:05Z", actual_cost_usd: 0 }),
  task("img_2", "image", { status: "failed", error_code: "PROVIDER_TIMEOUT", error_message: "ComfyUI did not answer", depends_on: ["img_1"] }),
  task("captions", "content", { status: "running", attempt: 2, depends_on: ["img_1"] }),
  task("qa_review", "qa", { depends_on: ["img_1", "img_2", "captions"] }),
];
const qa: QaReport = {
  verdict: "HUMAN_REVIEW",
  summary: "1 item needs review",
  checks: [
    { name: "image size", status: "pass", detail: "1080x1920" },
    { name: "caption length", status: "warn", detail: "one caption is long" },
  ],
};
const artifacts = [{ id: "a1", task_id: "id-img_1", filename: "promo_1.png", kind: "image", download_url: "/v1/artifacts/a1/preview" }] as unknown as Artifact[];

const draw = (j = job(), extra: { qa?: QaReport; artifacts?: Artifact[] } = { qa, artifacts }) =>
  render(
    <I18nProvider>
      <AgentGraph job={j} tasks={tasks} {...extra} />
    </I18nProvider>,
  );
const details = () => screen.getByRole("region", { name: /./ });

describe("AgentGraph V2: selection and details", () => {
  it("makes the Master Bot, every agent and every task satellite a selectable button", () => {
    draw();
    const nodes = screen.getAllByRole("button", { pressed: false });
    const labels = nodes.map((n) => n.getAttribute("aria-label") ?? "");
    expect(labels.some((l) => l.startsWith(th["graph.master"]))).toBe(true);
    for (const agent of ["image", "content", "qa"]) expect(labels.some((l) => l.startsWith(th[`agent.${agent}` as "agent.image"]))).toBe(true);
    expect(labels.some((l) => l.startsWith("img_2"))).toBe(true); // satellites of the image agent
  });

  it("shows an agent's real provider, execution time, cost, errors and files", () => {
    draw();
    const image = screen.getAllByRole("button").find((b) => b.getAttribute("aria-label")?.startsWith(th["agent.image"]))!;
    fireEvent.click(image);
    expect(image.getAttribute("aria-pressed")).toBe("true");
    const panel = details();
    expect(within(panel).getByRole("heading").textContent).toBe(th["agent.image"]);
    expect(panel.textContent).toContain("comfyui");
    expect(panel.textContent).toContain("1 นาที 5 วินาที"); // 10:00:00 → 10:01:05
    expect(panel.textContent).toContain("PROVIDER_TIMEOUT");
    expect(panel.textContent).toContain("ComfyUI did not answer");
    expect(within(panel).getByRole("link", { name: "promo_1.png" }).getAttribute("href")).toBe("/api/proxy/v1/artifacts/a1/preview");
    expect(panel.textContent).toContain(th["graph.details.costUnknown"]); // img_2 reports no cost: nothing invented
  });

  it("selects a single task satellite and shows what it waits for", () => {
    draw();
    fireEvent.click(screen.getAllByRole("button").find((b) => b.getAttribute("aria-label")?.startsWith("img_2"))!);
    expect(within(details()).getByRole("heading").textContent).toBe("img_2");
    expect(details().textContent).toContain(`${th["graph.details.dependsOn"]}img_1`);
  });

  it("works from the keyboard: Enter selects, Escape clears", () => {
    draw();
    const master = screen.getAllByRole("button").find((b) => b.getAttribute("aria-label")?.startsWith(th["graph.master"]))!;
    fireEvent.keyDown(master, { key: "Enter" });
    expect(within(details()).getByRole("heading").textContent).toBe(th["graph.master"]);
    fireEvent.keyDown(screen.getByRole("group", { name: /./ }), { key: "Escape" });
    expect(screen.queryByRole("region")).toBeNull();
  });
});

describe("AgentGraph V2: status rendering", () => {
  it("separates AI processing from human approval", () => {
    draw();
    expect(screen.getByRole("progressbar", { name: th["graph.ai.title"] }).getAttribute("aria-valuenow")).toBe("100");
    expect(screen.getByText(th["graph.approval.waiting"])).toBeTruthy();
    // the Master Bot itself reports the AI work as done while a person still has to approve
    expect(document.querySelector('[data-agent="master"] [data-alert]')).toBeNull();
  });

  it("explains the QA verdict and lists the checks", () => {
    draw();
    fireEvent.click(screen.getAllByRole("button").find((b) => b.getAttribute("aria-label")?.startsWith(th["agent.qa"]))!);
    const panel = details();
    expect(panel.textContent).toContain(th["graph.qa.HUMAN_REVIEW"]);
    expect(panel.textContent).toContain("1 item needs review");
    expect(within(panel).getByRole("list", { name: th["graph.qa.checks"] }).textContent).toContain("caption length");
  });

  it("falls back to the job's own QA fields when no report was loaded", () => {
    draw(job({ qa_verdict: "PASS", qa_summary: "All 6 checks passed.", require_approval: true }), {});
    fireEvent.click(screen.getAllByRole("button").find((b) => b.getAttribute("aria-label")?.startsWith(th["agent.qa"]))!);
    expect(details().textContent).toContain(th["graph.qa.PASS"]);
    expect(details().textContent).toContain(th["graph.qa.passApproval"]);
  });

  it("highlights failed and retrying agents", () => {
    draw();
    expect(document.querySelector('[data-agent="image"] [data-alert="failed"]')).not.toBeNull();
    expect(document.querySelector('[data-agent="content"] [data-alert="retrying"]')).not.toBeNull();
    const content = screen.getAllByRole("button").find((b) => b.getAttribute("aria-label")?.startsWith(th["agent.content"]))!;
    expect(content.getAttribute("aria-label")).toContain(th["graph.state.retrying"]);
  });

  it("draws dependency arrows from the task data, including into QA", () => {
    draw();
    const edges = [...document.querySelectorAll("[data-edge]")].map((e) => e.getAttribute("data-edge")).sort();
    expect(edges).toEqual(["content>qa", "image>content", "image>qa"]);
    expect(document.querySelector('[data-edge="image>content"] path[marker-end]')).not.toBeNull();
  });
});

describe("AgentGraph: offline workers", () => {
  const stuck = job({ status: "queued", progress: 0, approval_status: null, qa_verdict: null, finished_at: null });
  const planOnly = [task("plan", "planner", { status: "queued" })];
  const drawStuck = (offline?: string[]) =>
    render(
      <I18nProvider>
        <AgentGraph job={stuck} tasks={planOnly} offlineQueues={offline} />
      </I18nProvider>,
    );

  it("explains why a queued job is not starting and how to fix it", () => {
    drawStuck(["planner", "content", "image", "document", "video", "qa"]);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(th["graph.offline.title"].replace("{queues}", th["agent.planner"]));
    expect(alert.textContent).toContain("run-worker.ps1");
  });

  it("shows nothing when the workers are running", () => {
    drawStuck([]);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("AgentGraph V2: view controls", () => {
  it("zooms in, fits back to the nodes and resets the selection", () => {
    draw();
    const svg = screen.getByRole("group", { name: /./ });
    const fitted = svg.getAttribute("viewBox");
    fireEvent.click(screen.getByRole("button", { name: th["graph.zoomIn"] }));
    expect(svg.getAttribute("viewBox")).not.toBe(fitted);
    fireEvent.click(screen.getByRole("button", { name: th["graph.zoomReset"] }));
    expect(svg.getAttribute("viewBox")).toBe(fitted);
    fireEvent.keyDown(svg, { key: "+" });
    fireEvent.keyDown(svg, { key: "ArrowRight" });
    expect(svg.getAttribute("viewBox")).not.toBe(fitted);
    fireEvent.click(screen.getAllByRole("button").find((b) => b.getAttribute("aria-label")?.startsWith(th["graph.master"]))!);
    fireEvent.click(screen.getByRole("button", { name: th["graph.reset"] }));
    expect(svg.getAttribute("viewBox")).toBe(fitted);
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("fits the view to the nodes actually drawn, not the whole canvas", () => {
    draw();
    const [, , w, h] = screen.getByRole("group", { name: /./ }).getAttribute("viewBox")!.split(" ").map(Number);
    expect(w * h).toBeLessThan(1000 * 770); // three agents need less than the full five-agent canvas
  });
});
