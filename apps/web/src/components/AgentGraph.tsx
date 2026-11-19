"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { TaskStatusBadge } from "@/components/status";
import { cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { Agent, Job, Task, TaskStatus } from "@/lib/types";

// Live "command centre" view of a job: the Master Bot (planner/orchestrator) in the middle,
// one hub per agent, one satellite per task. Beams animate while work is being handed out.
// Everything is derived from the same job/task data the task cards use; nothing is simulated.

type NodeState = "idle" | "queued" | "working" | "done" | "failed" | "off";
type HubAgent = Exclude<Agent, "planner">;

const HUB_ORDER: HubAgent[] = ["content", "image", "document", "video", "qa"];

const COLORS: Record<NodeState, { core: string; glow: string }> = {
  idle: { core: "#a8b6d0", glow: "#64748b" },
  queued: { core: "#fdba74", glow: "#f97316" },
  working: { core: "#fde68a", glow: "#fbbf24" },
  done: { core: "#6ee7b7", glow: "#10b981" },
  failed: { core: "#fca5a5", glow: "#ef4444" },
  off: { core: "#64748b", glow: "#334155" },
};

interface Point {
  x: number;
  y: number;
}
interface Layout {
  w: number;
  h: number;
  master: Point;
  hubs: Record<HubAgent, Point>;
  r: { master: number; hub: number; sat: number; orbit: number };
  font: number;
}

const WIDE: Layout = {
  w: 1000,
  h: 770,
  master: { x: 500, y: 300 },
  hubs: {
    content: { x: 180, y: 172 },
    image: { x: 215, y: 535 },
    document: { x: 820, y: 172 },
    video: { x: 785, y: 535 },
    qa: { x: 500, y: 625 },
  },
  r: { master: 84, hub: 58, sat: 17, orbit: 112 },
  font: 14,
};

const NARROW: Layout = {
  w: 420,
  h: 955,
  master: { x: 210, y: 372 },
  hubs: {
    content: { x: 108, y: 127 },
    image: { x: 108, y: 645 },
    document: { x: 312, y: 127 },
    video: { x: 312, y: 645 },
    qa: { x: 210, y: 835 },
  },
  r: { master: 66, hub: 46, sat: 13, orbit: 80 },
  font: 13,
};

function taskState(status: TaskStatus): NodeState {
  switch (status) {
    case "running":
      return "working";
    case "queued":
      return "queued";
    case "succeeded":
      return "done";
    case "failed":
      return "failed";
    case "skipped":
    case "cancelled":
      return "off";
    default:
      return "idle";
  }
}

function hubState(tasks: Task[]): NodeState {
  if (tasks.length === 0) return "idle";
  const states = tasks.map((t) => taskState(t.status));
  if (states.includes("failed")) return "failed";
  if (states.includes("working")) return "working";
  if (states.includes("queued")) return "queued";
  if (states.every((s) => s === "done")) return "done";
  if (states.every((s) => s === "off")) return "off";
  if (states.some((s) => s === "done")) return "working"; // part-way through its tasks
  return "idle";
}

function masterState(job: Job, planner: Task | undefined): NodeState {
  switch (job.status) {
    case "queued":
      return "queued";
    case "planning":
    case "running":
    case "cancelling":
      return "working";
    case "planned":
    case "awaiting_approval":
      return "queued";
    case "completed":
      return "done";
    case "failed":
    case "rejected":
      return "failed";
    case "partial":
      return planner?.status === "failed" ? "failed" : "queued";
    default:
      return "off";
  }
}

/** Curved beam between two points; `bend` pushes the control point sideways. */
function arc(a: Point, b: Point, bend: number): string {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return `M ${a.x} ${a.y} Q ${mx - (dy / len) * bend} ${my + (dx / len) * bend} ${b.x} ${b.y}`;
}

function useNarrow(ref: React.RefObject<HTMLDivElement | null>): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < 640));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return narrow;
}

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(onChange: () => void): () => void {
  const mq = window.matchMedia(REDUCED_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReducedMotion, () => window.matchMedia(REDUCED_QUERY).matches, () => false);
}

// Deterministic star field (no Math.random: identical on server and client).
const STARS = Array.from({ length: 70 }, (_, i) => {
  const a = Math.sin(i * 127.1) * 43758.5453;
  const b = Math.sin(i * 311.7) * 24634.6345;
  const c = Math.sin(i * 74.7) * 13758.1123;
  return { x: a - Math.floor(a), y: b - Math.floor(b), o: 0.25 + (c - Math.floor(c)) * 0.55, r: i % 9 === 0 ? 1.6 : 0.9 };
});

type IconName = HubAgent | "master" | "lock";

// Original line icons on a 24x24 grid (stroke only).
const ICONS: Record<IconName, string[]> = {
  master: ["M12 9.2a2.8 2.8 0 1 0 0 5.6a2.8 2.8 0 1 0 0-5.6z", "M12 9.2V4.5M9.6 13.4L5.2 16.6M14.4 13.4l4.4 3.2", "M12 2.6a1.4 1.4 0 1 0 0 2.8a1.4 1.4 0 1 0 0-2.8z", "M4.2 16.2a1.4 1.4 0 1 0 0 2.8a1.4 1.4 0 1 0 0-2.8z", "M19.8 16.2a1.4 1.4 0 1 0 0 2.8a1.4 1.4 0 1 0 0-2.8z"],
  content: ["M4 5h16v11H11l-4 4v-4H4z", "M8 9h8M8 12.5h5"],
  image: ["M4 5h16v14H4z", "M4 16l5-5 4 4 3-3 4 4", "M8.5 8a1.3 1.3 0 1 0 0 2.6a1.3 1.3 0 1 0 0-2.6z"],
  document: ["M7 3h7l4 4v14H7z", "M14 3v4h4", "M10 12h5M10 16h5"],
  video: ["M4 6h12v12H4z", "M16 10.5l4.5-3v9l-4.5-3z", "M8.5 9.5v5l4-2.5z"],
  qa: ["M12 3l7 3v5c0 5-3 8-7 10c-4-2-7-5-7-10V6z", "M9 12l2.2 2.2L15.2 10"],
  lock: ["M7 11h10v8H7z", "M9 11V8a3 3 0 0 1 6 0v3"],
};

const ICON_COLORS: Record<IconName, [string, string, string]> = {
  master: ["#fde047", "#fb923c", "#e11d48"],
  content: ["#f9a8d4", "#ec4899", "#7c3aed"],
  image: ["#67e8f9", "#3b82f6", "#4f46e5"],
  document: ["#fde68a", "#f59e0b", "#ea580c"],
  video: ["#d8b4fe", "#a855f7", "#db2777"],
  qa: ["#bef264", "#22c55e", "#0d9488"],
  lock: ["#94a3b8", "#64748b", "#334155"],
};

function hexPoints(p: Point, r: number): string {
  // pointy-top hexagon
  return Array.from({ length: 6 }, (_, k) => {
    const a = (-90 + 60 * k) * (Math.PI / 180);
    return `${(p.x + Math.cos(a) * r).toFixed(1)},${(p.y + Math.sin(a) * r).toFixed(1)}`;
  }).join(" ");
}

/** Hexagon tile: bevelled frame in the status colour, an icon plate, and a counter tab on top. */
function HexNode({
  p,
  r,
  state,
  selected,
  pulse,
  icon,
  count,
}: {
  p: Point;
  r: number;
  state: NodeState;
  selected: boolean;
  pulse: boolean;
  icon?: IconName;
  count?: string;
}) {
  const c = COLORS[state];
  const origin = { transformOrigin: `${p.x}px ${p.y}px` };
  const lit = state !== "idle" && state !== "off";
  const clipId = useId();
  const glowing = state !== "off"; // locked tiles stay dark
  const fast = state === "working";
  const plate = r * 0.98; // icon plate side
  const py = p.y - r * 0.13; // plate sits high; the counter tab goes underneath
  const shown: IconName = state === "off" ? "lock" : (icon ?? "lock");
  const tabW = r * 0.78;
  const tabH = r * 0.3;
  const tabY = p.y + r * 0.64;
  return (
    <g>
      {/* breathing halo: stronger and faster while the agent is working */}
      <circle
        cx={p.x}
        cy={p.y}
        r={r * 2.05}
        fill={`url(#ag-glow-${state})`}
        className={lit ? "ag-breathe" : undefined}
        style={{ ...origin, animationDuration: state === "working" ? "1.6s" : "3.4s" }}
      />
      {pulse && <polygon points={hexPoints(p, r)} fill="none" stroke={c.core} strokeWidth={2} strokeLinejoin="round" className="ag-pulse" style={origin} />}
      <polygon points={hexPoints(p, r)} fill="#0a1020" stroke={c.core} strokeWidth={r > 20 ? 2.5 : 1.5} strokeLinejoin="round" />
      <polygon points={hexPoints(p, r)} fill={`url(#ag-hex-${state})`} strokeLinejoin="round" />
      <polygon points={hexPoints(p, r * 0.87)} fill="none" stroke="#ffffff" strokeOpacity={0.28} strokeWidth={1} strokeLinejoin="round" />
      {/* light running around the frame, with a soft bloom behind it */}
      {lit && r > 20 && (
        <g className="ag-chase" style={{ animationDuration: state === "working" ? "1.8s" : "4.5s" }}>
          <polygon points={hexPoints(p, r)} pathLength={100} fill="none" stroke={c.core} strokeOpacity={0.55} strokeWidth={9} strokeDasharray="22 78" strokeLinecap="round" strokeLinejoin="round" filter="url(#ag-bloom)" />
          <polygon points={hexPoints(p, r)} pathLength={100} fill="none" stroke="#ffffff" strokeWidth={2.6} strokeDasharray="16 84" strokeDashoffset={-3} strokeLinecap="round" strokeLinejoin="round" />
        </g>
      )}
      {icon && (
        <g pointerEvents="none">
          <clipPath id={clipId}>
            <rect x={p.x - plate / 2} y={py - plate / 2} width={plate} height={plate} rx={plate * 0.16} />
          </clipPath>
          {/* coloured bloom behind the plate, pulsing */}
          {glowing && (
            <rect
              x={p.x - plate / 2}
              y={py - plate / 2}
              width={plate}
              height={plate}
              rx={plate * 0.16}
              fill={`url(#ag-icon-${shown})`}
              filter="url(#ag-bloom)"
              className="ag-icon-glow"
              style={{ animationDuration: fast ? "1.2s" : "2.6s" }}
            />
          )}
          <rect x={p.x - plate / 2} y={py - plate / 2} width={plate} height={plate} rx={plate * 0.16} fill={`url(#ag-icon-${shown})`} stroke="#ffffff" strokeOpacity={0.85} strokeWidth={1.4} />
          {/* glossy highlight across the top-left of the plate */}
          <path
            d={`M ${p.x - plate / 2 + plate * 0.16} ${py - plate / 2} H ${p.x + plate * 0.3} L ${p.x - plate / 2} ${py + plate * 0.2} V ${py - plate / 2 + plate * 0.16} Q ${p.x - plate / 2} ${py - plate / 2} ${p.x - plate / 2 + plate * 0.16} ${py - plate / 2} Z`}
            fill="#ffffff"
            fillOpacity={0.22}
          />
          {/* sheen sweeping across the plate */}
          {glowing && shown !== "content" && (
            <g clipPath={`url(#${clipId})`}>
              <rect
                x={p.x - plate / 2}
                y={py - plate}
                width={plate * 0.3}
                height={plate * 2}
                fill="#ffffff"
                fillOpacity={0.55}
                transform={`rotate(24 ${p.x} ${py})`}
                className="ag-sheen"
                style={{ animationDuration: fast ? "1.6s" : "3.6s" }}
              />
            </g>
          )}
          <g
            className={glowing ? "ag-icon-pulse" : undefined}
            style={{ animationDuration: fast ? "1.2s" : "2.6s" }}
            transform={`translate(${p.x - plate * 0.37} ${py - plate * 0.37}) scale(${(plate * 0.74) / 24})`}
            fill="#ffffff"
            fillOpacity={0.3}
            stroke="#ffffff"
            strokeWidth={1.8}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {ICONS[shown].map((d) => (
              <path key={d} d={d} />
            ))}
          </g>
        </g>
      )}
      {count !== undefined && (
        <g pointerEvents="none">
          <rect x={p.x - tabW / 2} y={tabY - tabH / 2} width={tabW} height={tabH} rx={tabH / 2} fill="#0a1020" stroke={c.core} strokeOpacity={0.7} strokeWidth={0.8} />
          <text x={p.x} y={tabY + tabH * 0.32} textAnchor="middle" fontSize={tabH * 0.82} fontWeight={700} fill="#e8eef9" letterSpacing={0.6}>
            {count}
          </text>
        </g>
      )}
      {selected && <polygon points={hexPoints(p, r * 1.22)} fill="none" stroke="#22d3ee" strokeWidth={2.5} strokeDasharray="7 6" strokeLinejoin="round" className="ag-dash" />}
    </g>
  );
}

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

const MIN_ZOOM = 0.6;
const MAX_ZOOM = 4;

/**
 * Zoom (mouse wheel, toward the pointer) and pan (drag) for the graph, done by moving the SVG viewBox.
 * Zooming out at the minimum lets the wheel scroll the page again; double-click resets.
 */
function useZoomPan(svg: React.RefObject<SVGSVGElement | null>, w: number, h: number) {
  const [view, setView] = useState({ z: 1, x: 0, y: 0 });
  const zoomRef = useRef(view.z);
  useEffect(() => {
    zoomRef.current = view.z;
  });
  const clampView = (z: number, x: number, y: number) => {
    const vw = w / z;
    const vh = h / z;
    return { z, x: Math.min(Math.max(x, -vw / 2), w - vw / 2), y: Math.min(Math.max(y, -vh / 2), h - vh / 2) };
  };
  const toSvg = (clientX: number, clientY: number) => {
    const el = svg.current;
    const ctm = el?.getScreenCTM();
    if (!el || !ctm) return null;
    const pt = el.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    return pt.matrixTransform(ctm.inverse());
  };
  const zoomAt = (factor: number, clientX?: number, clientY?: number) =>
    setView((v) => {
      const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.z * factor));
      if (z === v.z) return v;
      const p =
        clientX !== undefined && clientY !== undefined ? toSvg(clientX, clientY) : null;
      const px = p?.x ?? v.x + w / v.z / 2;
      const py = p?.y ?? v.y + h / v.z / 2;
      const k = v.z / z; // new size / old size
      return clampView(z, px - (px - v.x) * k, py - (py - v.y) * k);
    });
  const reset = () => setView({ z: 1, x: 0, y: 0 });

  // Wheel: a non-passive listener, so zooming does not also scroll the page.
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const out = e.deltaY > 0;
      if (out && zoomRef.current <= MIN_ZOOM) return; // fully zoomed out: let the page scroll
      e.preventDefault();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  // Drag to pan (only after a few pixels, so clicking a node still selects it).
  const drag = useRef<{ id: number; sx: number; sy: number; x: number; y: number; moved: boolean } | null>(null);
  const justPanned = useRef(false);
  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, x: view.x, y: view.y, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    const ctm = svg.current?.getScreenCTM();
    if (!d || d.id !== e.pointerId || !ctm) return;
    const dx = e.clientX - d.sx;
    const dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 5) return;
    if (!d.moved) {
      d.moved = true;
      try {
        svg.current?.setPointerCapture(e.pointerId);
      } catch {
        // pointer already gone: panning still follows the moves we get
      }
    }
    setView((v) => clampView(v.z, d.x - dx / ctm.a, d.y - dy / ctm.d));
  };
  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (d && d.id === e.pointerId) {
      justPanned.current = d.moved;
      drag.current = null;
    }
  };
  // A drag must not count as a click on the node under the pointer.
  const onClickCapture = (e: React.MouseEvent) => {
    if (justPanned.current) {
      e.stopPropagation();
      justPanned.current = false;
    }
  };
  return {
    view,
    viewBox: `${view.x} ${view.y} ${w / view.z} ${h / view.z}`,
    zoomIn: () => zoomAt(1.25),
    zoomOut: () => zoomAt(0.8),
    reset,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp, onClickCapture, onDoubleClick: reset },
  };
}

export function AgentGraph({ job, tasks, fill = false }: { job: Job; tasks: Task[]; fill?: boolean }) {
  const { t, tx } = useI18n();
  const wrap = useRef<HTMLDivElement>(null);
  const narrow = useNarrow(wrap);
  const reduced = useReducedMotion();
  const L = narrow ? NARROW : WIDE;
  const svgRef = useRef<SVGSVGElement>(null);
  const zp = useZoomPan(svgRef, L.w, L.h);
  const [selected, setSelected] = useState<"master" | HubAgent | null>(null);

  // No manual memo: the React Compiler memoizes this derivation.
  const model = (() => {
    const byAgent = new Map<Agent, Task[]>();
    for (const task of tasks) byAgent.set(task.agent, [...(byAgent.get(task.agent) ?? []), task]);
    const planned = tasks.some((x) => x.agent !== "planner");
    // Before the plan exists, show the agents the user selected as idle hubs.
    const visible = HUB_ORDER.filter((a) => (planned ? byAgent.has(a) : a === "qa" || job.requested_agents.includes(a as never)));
    const keyAgent = new Map(tasks.map((x) => [x.key, x.agent]));
    const deps = new Set<string>();
    for (const task of tasks) {
      if (task.agent === "qa" || task.agent === "planner") continue; // QA depends on everything; drawn via the master
      for (const key of task.depends_on) {
        const from = keyAgent.get(key);
        if (from && from !== task.agent && from !== "planner") deps.add(`${from}>${task.agent}`);
      }
    }
    return { byAgent, visible, deps: [...deps].map((d) => d.split(">") as [HubAgent, HubAgent]) };
  })();

  const planner = model.byAgent.get("planner")?.[0];
  const mState = masterState(job, planner);
  const active = !["completed", "partial", "failed", "cancelled", "rejected"].includes(job.status);

  const hubLine = (agent: HubAgent, list: Task[]): string => {
    if (list.length === 0) return t("graph.idle");
    const done = list.filter((x) => x.status === "succeeded").length;
    const running = list.find((x) => x.status === "running");
    if (running) return clip(running.title || running.key, narrow ? 22 : 34);
    if (list.length > 1) return t("graph.progress", { done, total: list.length });
    return tx("taskStatus", list[0].status);
  };

  const selectedTasks = selected === "master" ? (planner ? [planner] : []) : selected ? (model.byAgent.get(selected) ?? []) : [];
  const router = useRouter();
  // Once QA has succeeded its node is a shortcut to the job's files: scroll to them when they
  // are on this page (Job Monitor), otherwise open the Job Monitor at that section.
  const qaDone = hubState(model.byAgent.get("qa") ?? []) === "done";
  const openFiles = () => {
    const section = document.getElementById("deliverables");
    if (section) section.scrollIntoView({ block: "start" }); // instant: smooth scrolling proved unreliable here
    else router.push(`/jobs/${job.id}#deliverables`);
  };
  const select = (id: "master" | HubAgent) => {
    if (id === "qa" && qaDone) return openFiles();
    setSelected((cur) => (cur === id ? null : id));
  };
  const keyActivate = (id: "master" | HubAgent) => (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      select(id);
    }
  };

  const label = (p: Point, r: number, title: string, line: string, state: NodeState, below = true) => {
    const w = narrow ? 150 : 210;
    const h = L.font * 2 + 16;
    const y = below ? p.y + r * 1.5 + 8 : p.y - r * 1.5 - 8 - h;
    return (
      <g pointerEvents="none">
        <rect x={p.x - w / 2} y={y} width={w} height={h} rx={9} fill="#0b1322" fillOpacity={0.88} stroke={COLORS[state].glow} strokeOpacity={0.45} />
        <text x={p.x} y={y + L.font + 4} textAnchor="middle" fontSize={L.font} fontWeight={600} fill="#e8eef9">
          {title}
        </text>
        <text x={p.x} y={y + L.font * 2 + 8} textAnchor="middle" fontSize={L.font - 2.5} fill="#9aa9c4">
          {line}
        </text>
      </g>
    );
  };

  const legend: Array<[NodeState, string]> = [
    ["idle", t("graph.legend.idle")],
    ["queued", t("graph.legend.queued")],
    ["working", t("graph.legend.working")],
    ["done", t("graph.legend.done")],
    ["failed", t("graph.legend.failed")],
  ];

  return (
    <div ref={wrap} className={cx(fill && "flex min-h-0 flex-1 flex-col")}>
      <div className={cx("relative overflow-hidden rounded-xl border border-line bg-[#050912]", fill && "min-h-[240px] flex-1")}>
        <svg
          ref={svgRef}
          viewBox={zp.viewBox}
          className={cx(
            "block touch-none select-none",
            fill ? "absolute inset-0 h-full w-full" : "h-auto w-full",
            zp.view.z !== 1 ? "cursor-grab active:cursor-grabbing" : "cursor-default",
          )}
          role="group"
          aria-label={t("graph.aria")}
          {...zp.handlers}
        >
          <defs>
            <radialGradient id="ag-space" gradientUnits="userSpaceOnUse" cx={L.w / 2} cy={L.h * 0.42} r={Math.max(L.w, L.h) * 0.8}>
              <stop offset="0%" stopColor="#13213f" />
              <stop offset="55%" stopColor="#0a1226" />
              <stop offset="100%" stopColor="#050912" />
            </radialGradient>
            {(Object.keys(COLORS) as NodeState[]).map((s) => (
              <g key={s}>
                <radialGradient id={`ag-core-${s}`} cx="35%" cy="30%" r="75%">
                  <stop offset="0%" stopColor="#ffffff" />
                  <stop offset="35%" stopColor={COLORS[s].core} />
                  <stop offset="100%" stopColor={COLORS[s].glow} />
                </radialGradient>
                <linearGradient id={`ag-hex-${s}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={COLORS[s].core} stopOpacity={s === "off" ? 0.35 : 0.95} />
                  <stop offset="45%" stopColor={COLORS[s].glow} stopOpacity={s === "off" ? 0.3 : 0.85} />
                  <stop offset="100%" stopColor={COLORS[s].glow} stopOpacity={0.25} />
                </linearGradient>
                <radialGradient id={`ag-glow-${s}`}>
                  <stop offset="0%" stopColor={COLORS[s].glow} stopOpacity={s === "off" ? 0.15 : 0.5} />
                  <stop offset="100%" stopColor={COLORS[s].glow} stopOpacity={0} />
                </radialGradient>
              </g>
            ))}
            {(Object.keys(ICON_COLORS) as IconName[]).map((name) => (
              <linearGradient key={name} id={`ag-icon-${name}`} x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor={ICON_COLORS[name][0]} />
                <stop offset="50%" stopColor={ICON_COLORS[name][1]} />
                <stop offset="100%" stopColor={ICON_COLORS[name][2]} />
              </linearGradient>
            ))}
            {/* user-space bounds: a straight beam has a zero-width bounding box, which would clip a box-relative filter */}
            <filter id="ag-bloom-beam" filterUnits="userSpaceOnUse" x={0} y={0} width={L.w} height={L.h}>
              <feGaussianBlur stdDeviation="7" />
            </filter>
            <filter id="ag-bloom-dot" x="-150%" y="-150%" width="400%" height="400%">
              <feGaussianBlur stdDeviation="2.2" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="ag-bloom" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="5" />
            </filter>
          </defs>

          {/* Space reaches well past the graph, so zooming out or a wide frame never shows an edge. */}
          <rect x={-L.w * 2} y={-L.h * 2} width={L.w * 5} height={L.h * 5} fill="url(#ag-space)" />
          {[-1, 0, 1].flatMap((tx) =>
            [-1, 0, 1].map((ty) =>
              STARS.map((s, i) => (
                <circle
                  key={`${tx}${ty}-${i}`}
                  cx={(s.x + tx) * L.w}
                  cy={(s.y + ty) * L.h}
                  r={s.r}
                  fill="#cfe0ff"
                  opacity={tx === 0 && ty === 0 ? s.o : s.o * 0.8}
                  className={tx === 0 && ty === 0 && i % 7 === 0 ? "ag-twinkle" : undefined}
                />
              )),
            ),
          )}
          <circle cx={L.master.x} cy={L.master.y} r={L.r.master * 2.6} fill="none" stroke="#3b82f6" strokeOpacity={0.18} strokeDasharray="2 9" className="ag-spin-slow" style={{ transformOrigin: `${L.master.x}px ${L.master.y}px` }} />

          {/* dependency links between agents (a finished agent hands its output on) */}
          {model.deps.map(([from, to]) => {
            if (!model.visible.includes(from) || !model.visible.includes(to)) return null;
            const d = arc(L.hubs[from], L.hubs[to], narrow ? 26 : 46);
            const fromDone = hubState(model.byAgent.get(from) ?? []) === "done";
            const toBusy = ["working", "queued"].includes(hubState(model.byAgent.get(to) ?? []));
            const passing = active && fromDone && toBusy;
            return (
              <g key={`${from}>${to}`} fill="none" strokeLinecap="round">
                {fromDone && <path d={d} stroke="#a78bfa" strokeOpacity={0.5} strokeWidth={10} filter="url(#ag-bloom-beam)" />}
                <path
                  d={d}
                  pathLength={100}
                  stroke={fromDone ? "#c4b5fd" : "#a78bfa"}
                  strokeOpacity={fromDone ? 0.95 : 0.35}
                  strokeWidth={fromDone ? 3.2 : 2}
                  strokeDasharray="1.5 3.5"
                  className={passing ? "ag-flow" : undefined}
                />
                {!reduced && passing && (
                  <circle r={5} fill="#ede9fe" stroke="none" filter="url(#ag-bloom-dot)">
                    <animateMotion dur="2.4s" repeatCount="indefinite" path={d} />
                  </circle>
                )}
              </g>
            );
          })}

          {/* beams from the Master Bot to each agent: bloom + coloured body + bright core */}
          {model.visible.map((agent, i) => {
            const list = model.byAgent.get(agent) ?? [];
            const state = hubState(list);
            const d = arc(L.master, L.hubs[agent], (i % 2 === 0 ? 1 : -1) * (agent === "qa" ? 0 : narrow ? 22 : 38));
            const flowing = active && (state === "working" || state === "queued");
            const lit = state !== "idle" && state !== "off";
            const c = COLORS[state];
            return (
              <g key={agent} fill="none" strokeLinecap="round">
                {lit && (
                  <path d={d} stroke={c.glow} strokeOpacity={flowing ? 0.9 : 0.6} strokeWidth={flowing ? 22 : 15} filter="url(#ag-bloom-beam)" className={flowing ? "ag-beam" : undefined} />
                )}
                <path d={d} stroke={c.glow} strokeOpacity={lit ? 0.95 : 0.35} strokeWidth={lit ? (flowing ? 8 : 6) : 3} />
                {lit && <path d={d} stroke={c.core} strokeOpacity={0.95} strokeWidth={flowing ? 3.2 : 2.2} />}
                {flowing && <path d={d} pathLength={100} stroke="#ffffff" strokeWidth={3.2} strokeDasharray="5 15" className="ag-flow" style={{ animationDuration: state === "working" ? "1.1s" : "2.2s" }} />}
                {!reduced && flowing &&
                  [0, 0.9, 1.8].map((delay) => (
                    <circle key={delay} r={state === "working" ? 6.5 : 5} fill="#ffffff" stroke="none" filter="url(#ag-bloom-dot)">
                      <animateMotion dur={state === "working" ? "2.7s" : "4s"} begin={`${delay}s`} repeatCount="indefinite" path={d} />
                    </circle>
                  ))}
              </g>
            );
          })}

          {/* agent hubs and their task satellites */}
          {model.visible.map((agent) => {
            const list = model.byAgent.get(agent) ?? [];
            const p = L.hubs[agent];
            const state = hubState(list);
            return (
              <g
                key={agent}
                role={agent === "qa" && qaDone ? "link" : "button"}
                tabIndex={0}
                aria-pressed={agent === "qa" && qaDone ? undefined : selected === agent}
                aria-label={agent === "qa" && qaDone ? `${tx("agent", agent)}: ${t("graph.openFiles")}` : `${tx("agent", agent)}: ${hubLine(agent, list)}`}
                onClick={() => select(agent)}
                onKeyDown={keyActivate(agent)}
                className="cursor-pointer outline-none [&:focus-visible>g>circle:last-of-type]:stroke-cyan"
              >
                {list.length > 1 && (
                  <>
                    <circle cx={p.x} cy={p.y} r={L.r.orbit} fill="none" stroke={COLORS[state].glow} strokeOpacity={0.3} strokeDasharray="2 6" />
                    {list.map((task, i) => {
                      const angle = (-Math.PI / 2) + (i / list.length) * Math.PI * 2;
                      const sp = { x: p.x + Math.cos(angle) * L.r.orbit, y: p.y + Math.sin(angle) * L.r.orbit };
                      const ts = taskState(task.status);
                      return (
                        <g key={task.id}>
                          <title>{`${task.title || task.key} · ${tx("taskStatus", task.status)}`}</title>
                          <HexNode p={sp} r={L.r.sat} state={ts} selected={false} pulse={!reduced && ts === "working"} />
                          <text x={sp.x} y={sp.y + L.r.sat * 0.36} textAnchor="middle" fontSize={L.r.sat} fontWeight={700} fill="#0a1020" pointerEvents="none">
                            {i + 1}
                          </text>
                        </g>
                      );
                    })}
                  </>
                )}
                <HexNode
                  p={p}
                  r={L.r.hub}
                  state={state}
                  selected={selected === agent}
                  pulse={!reduced && state === "working"}
                  icon={agent}
                  count={list.length > 0 ? String(list.length).padStart(2, "0") : undefined}
                />
                {label(p, list.length > 1 ? (L.r.orbit + L.r.sat) / 1.5 : L.r.hub * 0.78, tx("agent", agent), agent === "qa" && qaDone ? `${hubLine(agent, list)} · ${t("graph.openFiles")} ›` : hubLine(agent, list), state)}
              </g>
            );
          })}

          {/* Master Bot */}
          <g
            role="button"
            tabIndex={0}
            aria-pressed={selected === "master"}
            aria-label={`${t("graph.master")}: ${tx("status", job.status)}`}
            onClick={() => select("master")}
            onKeyDown={keyActivate("master")}
            className="cursor-pointer outline-none"
          >
            <HexNode
              p={L.master}
              r={L.r.master}
              state={mState}
              selected={selected === "master"}
              pulse={!reduced && mState === "working"}
              icon="master"
              count={`${Math.round(job.progress)}%`}
            />
            {label(L.master, L.r.master * 0.78, t("graph.master"), `${tx("status", job.status)} · ${Math.round(job.progress)}%`, mState)}
          </g>
        </svg>
        <div className="absolute bottom-2 left-2 z-10 flex items-center gap-1 rounded-lg border border-line bg-[#0b1322]/85 p-1 text-xs text-ink backdrop-blur">
          <button type="button" onClick={zp.zoomOut} aria-label={t("graph.zoomOut")} title={t("graph.zoomOut")} className="flex size-7 items-center justify-center rounded-md hover:bg-raised">
            −
          </button>
          <span className="min-w-10 text-center tabular-nums text-dim">{Math.round(zp.view.z * 100)}%</span>
          <button type="button" onClick={zp.zoomIn} aria-label={t("graph.zoomIn")} title={t("graph.zoomIn")} className="flex size-7 items-center justify-center rounded-md hover:bg-raised">
            +
          </button>
          <button type="button" onClick={zp.reset} aria-label={t("graph.zoomReset")} title={t("graph.zoomHint")} className="flex h-7 items-center justify-center rounded-md px-2 hover:bg-raised">
            {t("graph.zoomReset")}
          </button>
        </div>

        <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line/70 bg-[#070d1a]/90 px-3 py-2 text-xs text-dim">
          {legend.map(([state, text]) => (
            <li key={state} className="flex items-center gap-1.5">
              <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: COLORS[state].core, boxShadow: `0 0 6px ${COLORS[state].glow}` }} />
              {text}
            </li>
          ))}
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-1 w-6 rounded-full bg-warn shadow-[0_0_8px_var(--color-warn)]" />
            {t("graph.legend.beam")}
          </li>
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-0 w-5 border-t-2 border-dotted border-[#a78bfa]" />
            {t("graph.legend.dependency")}
          </li>
        </ul>
      </div>

      <div aria-live="polite" className={cx("mt-3 text-sm", !selected && "text-dim")}>
        {!selected ? (
          <p className="text-xs">{t("graph.selectHint")}</p>
        ) : (
          <div className="rounded-xl border border-cyan/40 bg-raised/60 p-3">
            <p className="mb-2 font-semibold text-ink">{selected === "master" ? t("graph.master") : tx("agent", selected)}</p>
            {selectedTasks.length === 0 ? (
              <p className="text-xs text-dim">{t("graph.idle")}</p>
            ) : (
              <ul className="space-y-2">
                {selectedTasks.map((task) => (
                  <li key={task.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    <TaskStatusBadge status={task.status} />
                    <span className="font-medium text-ink">{task.title || task.key}</span>
                    {task.provider && <span className="text-dim">{task.provider}{task.execution_location ? ` · ${tx("monitor.loc", task.execution_location)}` : ""}</span>}
                    {task.attempt > 1 && <span className="text-warn">{t("graph.attempt", { n: task.attempt, max: task.max_attempts })}</span>}
                    {task.error_code && <span className="font-mono text-bad">{task.error_code}</span>}
                    {task.routing_reason && <span className="basis-full text-dim">{task.routing_reason}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
