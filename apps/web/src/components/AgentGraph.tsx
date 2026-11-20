"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { GraphDetails, ProgressStrip, type Selection } from "@/components/graph/GraphDetails";
import { cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import {
  NARROW,
  WIDE,
  blockedByOfflineQueues,
  buildGraphModel,
  fitView,
  graphBounds,
  hubState,
  masterState,
  satellitePoints,
  taskState,
  type Box,
  type HubAgent,
  type NodeState,
  type Point,
} from "@/lib/agentGraph";
import type { Artifact, Job, QaReport, Task } from "@/lib/types";

// Live "command centre" view of a job: the Master Bot (planner/orchestrator) in the middle, one hub per
// agent, one satellite per task, arrows for real task dependencies. Beams animate while work is handed
// out. Everything is derived from the job, task, QA and artifact responses; nothing is simulated.

const COLORS: Record<NodeState, { core: string; glow: string }> = {
  idle: { core: "#a8b6d0", glow: "#64748b" },
  queued: { core: "#fdba74", glow: "#f97316" },
  working: { core: "#fde68a", glow: "#fbbf24" },
  retrying: { core: "#fb923c", glow: "#ea580c" },
  done: { core: "#6ee7b7", glow: "#10b981" },
  failed: { core: "#fca5a5", glow: "#ef4444" },
  off: { core: "#64748b", glow: "#334155" },
};

/** Curved beam between two points; `bend` pushes the control point sideways. */
function arc(a: Point, b: Point, bend: number): string {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return `M ${a.x} ${a.y} Q ${mx - (dy / len) * bend} ${my + (dx / len) * bend} ${b.x} ${b.y}`;
}

/** Shorten a segment at both ends so an arrow stops at the edge of the nodes instead of their centres. */
function trim(a: Point, b: Point, ra: number, rb: number): [Point, Point] {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return [
    { x: a.x + (dx / len) * ra, y: a.y + (dy / len) * ra },
    { x: b.x - (dx / len) * rb, y: b.y - (dy / len) * rb },
  ];
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
  const mq = window.matchMedia?.(REDUCED_QUERY);
  mq?.addEventListener("change", onChange);
  return () => mq?.removeEventListener("change", onChange);
}
function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReducedMotion, () => window.matchMedia?.(REDUCED_QUERY).matches ?? false, () => false);
}

// Deterministic star field (no Math.random: identical on server and client), drawn once as a tile.
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

/** Hexagon tile: bevelled frame in the status colour, an icon plate, a counter tab, and an alert badge. */
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
  const fast = state === "working" || state === "retrying";
  const plate = r * 0.98; // icon plate side
  const py = p.y - r * 0.13; // plate sits high; the counter tab goes underneath
  const shown: IconName = state === "off" ? "lock" : (icon ?? "lock");
  const tabW = r * 0.78;
  const tabH = r * 0.3;
  const tabY = p.y + r * 0.64;
  const alert = state === "failed" ? "!" : state === "retrying" ? "↻" : null;
  return (
    <g>
      {/* breathing halo: stronger and faster while the agent is working */}
      <circle
        cx={p.x}
        cy={p.y}
        r={r * 2.05}
        fill={`url(#ag-glow-${state})`}
        className={lit ? "ag-breathe" : undefined}
        style={{ ...origin, animationDuration: fast ? "1.6s" : "3.4s" }}
      />
      {pulse && <polygon points={hexPoints(p, r)} fill="none" stroke={c.core} strokeWidth={2} strokeLinejoin="round" className="ag-pulse" style={origin} />}
      {/* failed and retrying agents get a second, stronger outline so they stand out at any zoom */}
      {alert && <polygon points={hexPoints(p, r * 1.1)} fill="none" stroke={c.glow} strokeWidth={r > 20 ? 4 : 2.5} strokeDasharray={state === "retrying" ? "8 6" : undefined} strokeLinejoin="round" />}
      <polygon points={hexPoints(p, r)} fill="#0a1020" stroke={c.core} strokeWidth={r > 20 ? 2.5 : 1.5} strokeLinejoin="round" />
      <polygon points={hexPoints(p, r)} fill={`url(#ag-hex-${state})`} strokeLinejoin="round" />
      <polygon points={hexPoints(p, r * 0.87)} fill="none" stroke="#ffffff" strokeOpacity={0.28} strokeWidth={1} strokeLinejoin="round" />
      {/* light running around the frame, with a soft bloom behind it */}
      {lit && r > 20 && (
        <g className="ag-chase" style={{ animationDuration: fast ? "1.8s" : "4.5s" }}>
          <polygon points={hexPoints(p, r)} pathLength={100} fill="none" stroke={c.core} strokeOpacity={0.55} strokeWidth={9} strokeDasharray="22 78" strokeLinecap="round" strokeLinejoin="round" filter="url(#ag-bloom)" />
          <polygon points={hexPoints(p, r)} pathLength={100} fill="none" stroke="#ffffff" strokeWidth={2.6} strokeDasharray="16 84" strokeDashoffset={-3} strokeLinecap="round" strokeLinejoin="round" />
        </g>
      )}
      {icon && (
        <g pointerEvents="none">
          <clipPath id={clipId}>
            <rect x={p.x - plate / 2} y={py - plate / 2} width={plate} height={plate} rx={plate * 0.16} />
          </clipPath>
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
          <path
            d={`M ${p.x - plate / 2 + plate * 0.16} ${py - plate / 2} H ${p.x + plate * 0.3} L ${p.x - plate / 2} ${py + plate * 0.2} V ${py - plate / 2 + plate * 0.16} Q ${p.x - plate / 2} ${py - plate / 2} ${p.x - plate / 2 + plate * 0.16} ${py - plate / 2} Z`}
            fill="#ffffff"
            fillOpacity={0.22}
          />
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
      {alert && r > 10 && (
        <g pointerEvents="none" data-alert={state}>
          <circle cx={p.x + r * 0.78} cy={p.y - r * 0.78} r={Math.max(7, r * 0.26)} fill={c.glow} stroke="#0a1020" strokeWidth={2} />
          <text x={p.x + r * 0.78} y={p.y - r * 0.78 + Math.max(7, r * 0.26) * 0.38} textAnchor="middle" fontSize={Math.max(9, r * 0.32)} fontWeight={800} fill="#ffffff">
            {alert}
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

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 4;

/**
 * Zoom and pan by moving the SVG viewBox, relative to a base view fitted to the nodes actually drawn.
 * Wheel zooms toward the pointer (at the minimum it lets the page scroll), drag pans, double-click fits,
 * and the keyboard does the same (+ / − / 0 / arrow keys). While the user has not moved the view, it
 * re-fits when nodes appear or disappear.
 */
export function useZoomPan(svg: React.RefObject<SVGSVGElement | null>, base: Box) {
  const [view, setView] = useState<Box>(base);
  const [touched, setTouched] = useState(false);
  const [baseKey, setBaseKey] = useState(`${base.x},${base.y},${base.w},${base.h}`);
  const key = `${base.x},${base.y},${base.w},${base.h}`;
  if (key !== baseKey) {
    // adjusting state while rendering (the React-documented pattern for "reset when a prop changes")
    setBaseKey(key);
    if (!touched) setView(base);
  }
  const zoom = base.w / view.w;
  const zoomRef = useRef(zoom);
  useEffect(() => {
    zoomRef.current = zoom;
  });

  const clampView = (v: Box): Box => {
    // keep the centre of the view inside the fitted area
    const cx = Math.min(Math.max(v.x + v.w / 2, base.x), base.x + base.w);
    const cy = Math.min(Math.max(v.y + v.h / 2, base.y), base.y + base.h);
    return { x: cx - v.w / 2, y: cy - v.h / 2, w: v.w, h: v.h };
  };
  const toSvg = (clientX: number, clientY: number) => {
    const el = svg.current;
    const ctm = el?.getScreenCTM?.();
    if (!el || !ctm) return null;
    const pt = el.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    return pt.matrixTransform(ctm.inverse());
  };
  const zoomAt = (factor: number, clientX?: number, clientY?: number) => {
    setTouched(true);
    setView((v) => {
      const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, (base.w / v.w) * factor));
      const w = base.w / z;
      const h = base.h / z;
      if (Math.abs(w - v.w) < 0.01) return v;
      const p = clientX !== undefined && clientY !== undefined ? toSvg(clientX, clientY) : null;
      const px = p?.x ?? v.x + v.w / 2;
      const py = p?.y ?? v.y + v.h / 2;
      const k = w / v.w;
      return clampView({ x: px - (px - v.x) * k, y: py - (py - v.y) * k, w, h });
    });
  };
  const pan = (fx: number, fy: number) => {
    setTouched(true);
    setView((v) => clampView({ ...v, x: v.x + v.w * fx, y: v.y + v.h * fy }));
  };
  const fit = () => {
    setTouched(false);
    setView(base);
  };

  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY > 0 && zoomRef.current <= MIN_ZOOM) return; // fully zoomed out: let the page scroll
      e.preventDefault();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  // Drag to pan (only after a few pixels, so clicking a node still selects it).
  const drag = useRef<{ id: number; sx: number; sy: number; v: Box; moved: boolean } | null>(null);
  const justPanned = useRef(false);
  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, v: view, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    const ctm = svg.current?.getScreenCTM?.();
    if (!d || d.id !== e.pointerId || !ctm) return;
    const dx = e.clientX - d.sx;
    const dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 5) return;
    if (!d.moved) {
      d.moved = true;
      setTouched(true);
      try {
        svg.current?.setPointerCapture(e.pointerId);
      } catch {
        // pointer already gone: panning still follows the moves we get
      }
    }
    setView(clampView({ ...d.v, x: d.v.x - dx / ctm.a, y: d.v.y - dy / ctm.d }));
  };
  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (d && d.id === e.pointerId) {
      justPanned.current = d.moved;
      drag.current = null;
    }
  };
  const onClickCapture = (e: React.MouseEvent) => {
    if (justPanned.current) {
      e.stopPropagation();
      justPanned.current = false;
    }
  };
  return {
    zoom,
    viewBox: `${view.x.toFixed(1)} ${view.y.toFixed(1)} ${view.w.toFixed(1)} ${view.h.toFixed(1)}`,
    zoomIn: () => zoomAt(1.25),
    zoomOut: () => zoomAt(0.8),
    pan,
    fit,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp, onClickCapture, onDoubleClick: fit },
  };
}

export function AgentGraph({
  job,
  tasks,
  fill = false,
  qa,
  artifacts,
  offlineQueues,
}: {
  job: Job;
  tasks: Task[];
  fill?: boolean;
  /** Agent queues with no live worker (dashboard summary), to explain a job that cannot start. */
  offlineQueues?: readonly string[];
  /** The job's QA report, when the page already has it (verdict explanation and checks). */
  qa?: QaReport | null;
  /** The job's files, when the page already has them (shown per task in the details panel). */
  artifacts?: Artifact[];
}) {
  const { t, tx } = useI18n();
  const wrap = useRef<HTMLDivElement>(null);
  const narrow = useNarrow(wrap);
  const reduced = useReducedMotion();
  const L = narrow ? NARROW : WIDE;
  const svgRef = useRef<SVGSVGElement>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const active = !["completed", "partial", "failed", "cancelled", "rejected", "awaiting_approval"].includes(job.status);
  // Running time in the details panel ticks once a second while the job is active.
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);

  // No manual memo: the React Compiler memoizes these derivations.
  const model = buildGraphModel(job, tasks);
  const base = fitView(graphBounds(model, L));
  const zp = useZoomPan(svgRef, base);
  const mState = masterState(job, model.planner);
  const hubR = (agent: HubAgent) => ((model.byAgent.get(agent)?.length ?? 0) > 1 ? L.r.orbit * 0.62 : L.r.hub * 1.08);

  const hubLine = (list: Task[]): string => {
    if (list.length === 0) return t("graph.idle");
    const failed = list.filter((x) => x.status === "failed").length;
    if (failed) return t("graph.failedCount", { n: String(failed) });
    const retrying = list.find((x) => taskState(x) === "retrying");
    if (retrying) return t("graph.details.retrying", { n: String(retrying.attempt), max: String(retrying.max_attempts) });
    const done = list.filter((x) => x.status === "succeeded").length;
    const running = list.find((x) => x.status === "running");
    if (running) return clip(running.title || running.key, narrow ? 22 : 34);
    if (list.length > 1) return t("graph.progress", { done, total: list.length });
    return tx("taskStatus", list[0].status);
  };

  const router = useRouter();
  // The QA node's details offer a shortcut to the job's files: scroll to them when they are on this page
  // (Job Monitor), otherwise open the Job Monitor at that section.
  const openFiles = () => {
    const section = document.getElementById("deliverables");
    if (section) section.scrollIntoView({ block: "start" });
    else router.push(`/jobs/${job.id}#deliverables`);
  };
  const same = (a: Selection | null, b: Selection) =>
    !!a && a.kind === b.kind && (a.kind !== "agent" || a.agent === (b as { agent: HubAgent }).agent) && (a.kind !== "task" || a.taskId === (b as { taskId: string }).taskId);
  const select = (s: Selection) => setSelection((cur) => (same(cur, s) ? null : s));
  const nodeProps = (s: Selection, label: string) => ({
    role: "button" as const,
    tabIndex: 0,
    "aria-pressed": same(selection, s),
    "aria-label": label,
    "aria-controls": "graph-details",
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation();
      select(s);
    },
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        select(s);
      }
    },
    className: "cursor-pointer outline-none [&:focus-visible_.ag-focus]:opacity-100",
  });
  // Keyboard on the graph surface: zoom, pan, fit, clear the selection.
  const onGraphKey = (e: React.KeyboardEvent) => {
    const k = e.key;
    if (k === "+" || k === "=") zp.zoomIn();
    else if (k === "-" || k === "_") zp.zoomOut();
    else if (k === "0") zp.fit();
    else if (k === "ArrowLeft") zp.pan(-0.1, 0);
    else if (k === "ArrowRight") zp.pan(0.1, 0);
    else if (k === "ArrowUp") zp.pan(0, -0.1);
    else if (k === "ArrowDown") zp.pan(0, 0.1);
    else if (k === "Escape" && selection) setSelection(null);
    else return;
    e.preventDefault();
  };
  const resetAll = () => {
    setSelection(null);
    zp.fit();
  };

  const focusRing = (p: Point, r: number) => (
    <polygon points={hexPoints(p, r * 1.3)} fill="none" stroke="#22d3ee" strokeWidth={3} strokeLinejoin="round" opacity={0} className="ag-focus" pointerEvents="none" />
  );

  const label = (p: Point, r: number, title: string, line: string, state: NodeState) => {
    const w = L.labelW;
    const h = L.font * 2 + 16;
    const y = p.y + r * 1.5 + 8;
    return (
      <g pointerEvents="none">
        <rect x={p.x - w / 2} y={y} width={w} height={h} rx={9} fill="#0b1322" fillOpacity={0.88} stroke={COLORS[state].glow} strokeOpacity={state === "failed" || state === "retrying" ? 0.9 : 0.45} />
        <text x={p.x} y={y + L.font + 4} textAnchor="middle" fontSize={L.font} fontWeight={600} fill="#e8eef9">
          {title}
        </text>
        <text x={p.x} y={y + L.font * 2 + 8} textAnchor="middle" fontSize={L.font - 2.5} fill={state === "failed" ? "#fca5a5" : state === "retrying" ? "#fdba74" : "#9aa9c4"}>
          {line}
        </text>
      </g>
    );
  };

  const legend: Array<[NodeState, string]> = [
    ["idle", t("graph.legend.idle")],
    ["queued", t("graph.legend.queued")],
    ["working", t("graph.legend.working")],
    ["retrying", t("graph.legend.retrying")],
    ["done", t("graph.legend.done")],
    ["failed", t("graph.legend.failed")],
  ];
  const qaVerdict = qa?.verdict ?? job.qa_verdict;
  const blocked = blockedByOfflineQueues(job, tasks, offlineQueues);

  return (
    <div ref={wrap} className={cx(fill && "flex min-h-0 flex-1 flex-col")}>
      {blocked.length > 0 && (
        <div role="alert" className="mb-2 rounded-lg border border-warn/60 bg-warn/10 px-3 py-2 text-sm text-ink">
          <p className="font-semibold text-warn">
            {t("graph.offline.title", { queues: blocked.map((q) => tx("agent", q)).join(", ") })}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed">{t("graph.offline.hint")}</p>
        </div>
      )}
      <ProgressStrip job={job} tasks={tasks} />
      <div className={cx("relative overflow-hidden rounded-xl border border-line bg-[#050912]", fill && "min-h-[240px] flex-1")}>
        <svg
          ref={svgRef}
          viewBox={zp.viewBox}
          className={cx(
            "block touch-none select-none outline-none focus-visible:ring-2 focus-visible:ring-cyan",
            fill ? "absolute inset-0 h-full w-full" : "h-auto max-h-[78vh] w-full",
            zp.zoom !== 1 ? "cursor-grab active:cursor-grabbing" : "cursor-default",
          )}
          role="group"
          tabIndex={0}
          aria-label={`${t("graph.aria")}. ${t("graph.keys")}`}
          onKeyDown={onGraphKey}
          {...zp.handlers}
        >
          <defs>
            <radialGradient id="ag-space" gradientUnits="userSpaceOnUse" cx={L.w / 2} cy={L.h * 0.42} r={Math.max(L.w, L.h) * 0.8}>
              <stop offset="0%" stopColor="#13213f" />
              <stop offset="55%" stopColor="#0a1226" />
              <stop offset="100%" stopColor="#050912" />
            </radialGradient>
            {/* One tile of stars, repeated: 70 circles instead of 630. */}
            <pattern id="ag-stars" patternUnits="userSpaceOnUse" width={L.w} height={L.h}>
              {STARS.map((s, i) => (
                <circle key={i} cx={s.x * L.w} cy={s.y * L.h} r={s.r} fill="#cfe0ff" opacity={s.o} />
              ))}
            </pattern>
            {(Object.keys(COLORS) as NodeState[]).map((s) => (
              <g key={s}>
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
            <marker id="ag-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#c4b5fd" />
            </marker>
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
          <g onClick={() => setSelection(null)}>
            <rect x={-L.w * 2} y={-L.h * 2} width={L.w * 5} height={L.h * 5} fill="url(#ag-space)" />
            <rect x={-L.w * 2} y={-L.h * 2} width={L.w * 5} height={L.h * 5} fill="url(#ag-stars)" />
          </g>
          {!reduced &&
            STARS.filter((_, i) => i % 7 === 0).map((s, i) => (
              <circle key={i} cx={s.x * L.w} cy={s.y * L.h} r={s.r} fill="#cfe0ff" opacity={s.o} className="ag-twinkle" pointerEvents="none" />
            ))}
          <circle cx={L.master.x} cy={L.master.y} r={L.r.master * 2.6} fill="none" stroke="#3b82f6" strokeOpacity={0.18} strokeDasharray="2 9" className="ag-spin-slow" style={{ transformOrigin: `${L.master.x}px ${L.master.y}px` }} pointerEvents="none" />

          {/* beams from the Master Bot to each agent: bloom + coloured body + bright core */}
          {model.visible.map((agent, i) => {
            const list = model.byAgent.get(agent) ?? [];
            const state = hubState(list);
            const d = arc(L.master, L.hubs[agent], (i % 2 === 0 ? 1 : -1) * (agent === "qa" ? 0 : narrow ? 22 : 38));
            const flowing = active && (state === "working" || state === "queued" || state === "retrying");
            const lit = state !== "idle" && state !== "off";
            const c = COLORS[state];
            return (
              <g key={agent} fill="none" strokeLinecap="round" pointerEvents="none">
                {lit && <path d={d} stroke={c.glow} strokeOpacity={flowing ? 0.9 : 0.6} strokeWidth={flowing ? 22 : 15} filter="url(#ag-bloom-beam)" className={flowing ? "ag-beam" : undefined} />}
                <path d={d} stroke={c.glow} strokeOpacity={lit ? 0.95 : 0.35} strokeWidth={lit ? (flowing ? 8 : 6) : 3} />
                {lit && <path d={d} stroke={c.core} strokeOpacity={0.95} strokeWidth={flowing ? 3.2 : 2.2} />}
                {flowing && <path d={d} pathLength={100} stroke="#ffffff" strokeWidth={3.2} strokeDasharray="5 15" className="ag-flow" style={{ animationDuration: state === "working" ? "1.1s" : "2.2s" }} />}
                {!reduced &&
                  flowing &&
                  [0, 0.9, 1.8].map((delay) => (
                    <circle key={delay} r={state === "working" ? 6.5 : 5} fill="#ffffff" stroke="none" filter="url(#ag-bloom-dot)">
                      <animateMotion dur={state === "working" ? "2.7s" : "4s"} begin={`${delay}s`} repeatCount="indefinite" path={d} />
                    </circle>
                  ))}
              </g>
            );
          })}

          {/* dependency arrows between agents, from each task's depends_on (QA included) */}
          {model.edges.map((edge) => {
            if (!model.visible.includes(edge.from) || !model.visible.includes(edge.to)) return null;
            const [a, b] = trim(L.hubs[edge.from], L.hubs[edge.to], hubR(edge.from), hubR(edge.to) + 6);
            const d = arc(a, b, narrow ? 26 : 46);
            const fromDone = hubState(model.byAgent.get(edge.from) ?? []) === "done";
            const toBusy = ["working", "queued", "retrying"].includes(hubState(model.byAgent.get(edge.to) ?? []));
            const passing = active && fromDone && toBusy;
            return (
              <g key={`${edge.from}>${edge.to}`} fill="none" strokeLinecap="round" data-edge={`${edge.from}>${edge.to}`}>
                <title>{`${tx("agent", edge.from)} → ${tx("agent", edge.to)}: ${edge.links.length} ${t("graph.legend.dependency")}`}</title>
                {fromDone && <path d={d} stroke="#a78bfa" strokeOpacity={0.5} strokeWidth={10} filter="url(#ag-bloom-beam)" />}
                <path
                  d={d}
                  pathLength={100}
                  stroke={fromDone ? "#c4b5fd" : "#a78bfa"}
                  strokeOpacity={fromDone ? 0.95 : 0.55}
                  strokeWidth={fromDone ? 3.2 : 2.2}
                  strokeDasharray="1.5 3.5"
                  markerEnd="url(#ag-arrow)"
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

          {/* agent hubs and their task satellites (each one selectable) */}
          {model.visible.map((agent) => {
            const list = model.byAgent.get(agent) ?? [];
            const p = L.hubs[agent];
            const state = hubState(list);
            const sats = list.length > 1 ? satellitePoints(p, list.length, L.r.orbit) : [];
            const line = agent === "qa" && qaVerdict ? `${qaVerdict} · ${hubLine(list)}` : hubLine(list);
            return (
              <g key={agent} data-agent={agent}>
                {sats.length > 0 && <circle cx={p.x} cy={p.y} r={L.r.orbit} fill="none" stroke={COLORS[state].glow} strokeOpacity={0.3} strokeDasharray="2 6" pointerEvents="none" />}
                {list.length > 1 &&
                  list.map((task, i) => {
                    const sp = sats[i];
                    const ts = taskState(task);
                    const sel: Selection = { kind: "task", taskId: task.id };
                    return (
                      <g key={task.id} {...nodeProps(sel, `${task.title || task.key}: ${t(`graph.state.${ts}`)}`)} data-task={task.key}>
                        <HexNode p={sp} r={L.r.sat} state={ts} selected={same(selection, sel)} pulse={!reduced && (ts === "working" || ts === "retrying")} />
                        <text x={sp.x} y={sp.y + L.r.sat * 0.36} textAnchor="middle" fontSize={L.r.sat} fontWeight={700} fill="#0a1020" pointerEvents="none">
                          {i + 1}
                        </text>
                        {focusRing(sp, L.r.sat)}
                      </g>
                    );
                  })}
                <g {...nodeProps({ kind: "agent", agent }, `${tx("agent", agent)}: ${t(`graph.state.${state}`)}. ${line}`)}>
                  <HexNode
                    p={p}
                    r={L.r.hub}
                    state={state}
                    selected={same(selection, { kind: "agent", agent })}
                    pulse={!reduced && (state === "working" || state === "retrying")}
                    icon={agent}
                    count={list.length > 0 ? String(list.length).padStart(2, "0") : undefined}
                  />
                  {focusRing(p, L.r.hub)}
                  {label(p, list.length > 1 ? (L.r.orbit + L.r.sat) / 1.5 : L.r.hub * 0.78, tx("agent", agent), line, state)}
                </g>
              </g>
            );
          })}

          {/* Master Bot: AI processing only (human approval is shown above the graph) */}
          <g {...nodeProps({ kind: "master" }, `${t("graph.master")}: ${tx("status", job.status)}, ${Math.round(job.progress)}%`)} data-agent="master">
            <HexNode
              p={L.master}
              r={L.r.master}
              state={mState}
              selected={same(selection, { kind: "master" })}
              pulse={!reduced && mState === "working"}
              icon="master"
              count={`${Math.round(job.progress)}%`}
            />
            {focusRing(L.master, L.r.master)}
            {label(L.master, L.r.master * 0.78, t("graph.master"), `${tx("status", job.status)} · ${Math.round(job.progress)}%`, mState)}
          </g>
        </svg>
        <div className="absolute bottom-2 left-2 z-10 flex items-center gap-1 rounded-lg border border-line bg-[#0b1322]/85 p-1 text-xs text-ink backdrop-blur">
          <button type="button" onClick={zp.zoomOut} aria-label={t("graph.zoomOut")} title={t("graph.zoomOut")} className="flex size-7 items-center justify-center rounded-md hover:bg-raised">
            −
          </button>
          <span className="min-w-10 text-center tabular-nums text-dim" aria-live="polite">
            {Math.round(zp.zoom * 100)}%
          </span>
          <button type="button" onClick={zp.zoomIn} aria-label={t("graph.zoomIn")} title={t("graph.zoomIn")} className="flex size-7 items-center justify-center rounded-md hover:bg-raised">
            +
          </button>
          <button type="button" onClick={zp.fit} title={t("graph.zoomHint")} className="flex h-7 items-center justify-center rounded-md px-2 hover:bg-raised">
            {t("graph.zoomReset")}
          </button>
          <button type="button" onClick={resetAll} title={t("graph.resetHint")} className="flex h-7 items-center justify-center rounded-md px-2 hover:bg-raised">
            {t("graph.reset")}
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
            {t("graph.legend.dependency")} →
          </li>
        </ul>
      </div>

      <div id="graph-details" aria-live="polite" className={cx("mt-3 text-sm", !selection && "text-dim")}>
        {!selection ? (
          <p className="text-xs">{t("graph.selectHint")}</p>
        ) : (
          <GraphDetails
            selection={selection}
            job={job}
            tasks={tasks}
            model={model}
            qa={qa}
            artifacts={artifacts}
            now={now}
            onClose={() => setSelection(null)}
            onOpenFiles={openFiles}
          />
        )}
      </div>
    </div>
  );
}
